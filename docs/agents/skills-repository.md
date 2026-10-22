# Skills repository

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
