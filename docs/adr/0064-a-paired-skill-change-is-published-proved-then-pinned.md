# A paired Skill change is published, proved, then pinned

**Status:** accepted

**Extends** [ADR-0023](0023-pinned-external-skill-catalog.md) and
[ADR-0025](0025-installed-skill-catalog.md).

**Records the authoring rule that survives**
[ADR-0034](0034-contract-carrying-skills-are-authored-upstream.md), which
[ADR-0046](0046-continuation-is-decommissioned.md) superseded.

Decided by [#634](https://github.com/bradcstevens/git-loopy/issues/634).

ADR-0023 named `bradcstevens/git-loopy-skills` the source of record and said
moving the pin is a reviewed edit made together with the catalog change it
justifies. It did not say an Iteration in this checkout may edit that
repository, or in what order. ADR-0025 made the installed catalog the only
Skill source a Run reads, and made a hand-edit there something the next Run
repairs. ADR-0034 then said a contract-carrying Skill is authored upstream and
mirrored here. ADR-0046 superseded that decision because its subject —
Continuation, and the mirror that existed to test it — has no referent, and
retired the term **Contract-carrying Skill**. What it did not do is name a new
authoring surface, or say that a Skill prompt may now be written in this
checkout.

A ticket whose fix is a Skill prompt therefore had no route. The Run
instructions scoped an Iteration to this worktree. `AGENTS.md` described one
tech stack and a feedback-loop table with no row that leaves this checkout —
correctly, because a row is a gate, and a gate that reaches the network is red
for a reason no change here caused. The missing sentence was the other one.

## Decision

### Authored upstream, mirrored nowhere

A Skill prompt is authored in `bradcstevens/git-loopy-skills` and mirrored
nowhere. ADR-0034's mirror was a fixture for `git-loopy continuation` requests.
ADR-0046 removed the subject and the mirror with it. That supersession is not
permission to author a prompt in this repository. The term Contract-carrying
Skill stays retired: it denoted a Skill whose instructions invoke a
`git-loopy` subcommand, and after ADR-0046 no Skill does. What survives, and
what this ADR states, is the authoring location.

### A working surface, not a gate row

The Skills repository is a repository this project changes. A working clone is
expected as a sibling of this checkout, `../git-loopy-skills` relative to the
repository root, acquired with:

```bash
git clone https://github.com/bradcstevens/git-loopy-skills.git ../git-loopy-skills
```

It is not `<config-home>/git-loopy/skills/`, not
`<config-home>/git-loopy/skill-catalog/`, and not
`.git-loopy/skill-source/`. The first two are the install a Run refreshes; the
third is a force-checkout of the pin. Pointing any of them at work in progress
discards it.

The Integration gate still reaches no network. The declared feedback loops
still run from this repository alone. Proving a candidate is an Iteration's
own step, never a row in that table. An unreachable upstream must not make
Integration red.

### Publish, prove, pin — the pin is last

The order is a requirement. A pin that moves before the proof is the failure
this sequence exists to prevent, because the pin is what every installation
refreshes from.

1. **Publish** the Skill change upstream, as a full 40-character SHA. An
   uncommitted tree is not a published revision.
2. **Prove** that published revision with
   `python -m git_loopy.skill_candidate` against a checkout of that SHA, clean
   of later edits. A pass does not move the pin. A dirty tree is the preview
   [#633](https://github.com/bradcstevens/git-loopy/issues/633) already
   provides, not the proof that authorizes the pin.
3. **Pin last.** Only after that proof passes, edit `skill_source.json` to
   that SHA.

An unattended Iteration whose issue calls for a Skill prompt change may make
that paired change, bounded to the issue. An Iteration that cannot complete
the upstream half does not move the pin, and reports why.

The git-loopy commit names the upstream revision the pin moves to and the
Skill edit it carries. A reader is not left inferring which edit a pin bump
is.

## Considered options

- **Add a feedback-loop row that clones the Skills repository and proves it** —
  rejected because every row is a blocking gate, and an unreachable upstream
  would make Integration red for a reason no change here caused. That is the
  same exclusion ADR-0023's acquisition command already has.
- **Author the prompt here and sync upstream** — rejected. It inverts
  ADR-0023's source of record and reopens the two-surface divergence ADR-0034
  existed to end. ADR-0046 did not reopen it.
- **Let an Iteration bump the pin and leave the Skill edit for a follow-up** —
  rejected. The pin bump is what every installation refreshes from. A pin with
  nothing behind it is the failure this sequence exists to prevent.

## Consequences

- `AGENTS.md` and the Run instructions state the working surface, the order,
  and the refusal to move the pin when the upstream half cannot be completed.
- The operator refresh procedure in `docs/skill-catalog-source.md` follows the
  same order. Judging a dirty clone remains a preview and does not authorize
  the pin.
- Earlier ADRs keep their text. ADR-0023, ADR-0025, ADR-0034, and ADR-0046
  gain a pointer here so the relationship is recorded rather than left
  implicit.
