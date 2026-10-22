# Reopening an unpublished Release target withdraws its stable commit

**Status:** accepted design; implementation acceptance is tracked by the delivery issues.

Amends [ADR-0059](0059-verify-the-promoted-snapshot-before-publishing-an-immutable-tag.md):
its "before publication, a candidate may instead be repaired and reverified" now says how,
for a stable commit the trunk already carries. It relies on the operator's by-exception
promotion that [ADR-0079](0079-a-human-opens-a-major-line-and-the-1-0-0-cut-waits-for-the-python-path.md)
recognizes. Decided in [#892](https://github.com/bradcstevens/git-loopy/issues/892).

The **Promotion** walks the trunk's untagged stable commits oldest first and stops at its
first refusal. For v0.11.3 that walk finds one commit, `ef7fadb3`, which can never be
published. Its tree predates the **Unavailable proof** declaration, which
[ADR-0094](0094-a-promotion-proves-the-installation-it-ships-and-declares-the-live-proof-it-cannot-obtain.md)
requires inside the tagged commit, and its notes still describe the one-off exception. Its
Release commit also changes `model-roster.json`. The unchanged **Rehearsal** refuses that
before the gate, because a Release commit may advance only the live Release fixture among
the Conformance fixtures. So `ef7fadb3` refuses ahead of any later candidate, on every
push, forever.

Nothing automated can declare 0.11.3 again. From a stable `VERSION` the ratchet only rises,
a stage advance refuses a stable line, and a milestone promotes only a prerelease line. The
tagged commit must itself carry the `VERSION` bump, so the trunk head cannot be tagged
either.

## A Reopen withdraws the unpublished stable commit

A **Reopen** moves an unpublished stable Release target back onto its prerelease line, for
example from `0.11.3` to `0.11.3-rc.1`. It withdraws every earlier untagged stable commit
that declares that target, and the target's next promotion becomes its candidate. The
Promotion and `release_rehearsal` share this one walk, and it names each commit it
withdraws.

Only an operator's by-exception commit can Reopen a target. No Run advance, stage advance
or milestone moves a stable value back onto its own prerelease line. So a revert that
restores a stable `VERSION` withdraws nothing, and the content a set of notes describes can
never be swapped silently. The walk keeps its order, and it still stops at the first refusal
among the candidates that remain. Every rule a candidate must pass is unchanged, and so is
the tagged trust policy that states what a Release proves.

A Reopen and its re-promotion must land on the trunk's first-parent history. A squash
collapses them into one commit with no `VERSION` change. A merge commit hides both from the
walk, because the merge leaves `VERSION` where it was. That includes a Run's `--no-ff`
Integration, so no Run can carry one.

## 0.11.3 is repaired by a Reopen and a re-promotion

Two by-exception commits, both written by the production Release-version writer, land
consecutively on `main`:

- The Reopen moves `VERSION` to `0.11.3-rc.1` and records itself in a development fragment.
- The re-promotion moves `VERSION` back to `0.11.3`. It is the candidate: it carries the
  declaration, and its parent's `VERSION` differs.

v0.11.3 ships the trunk as it stands at the re-promotion, as #873 intended with "the
complete current main snapshot". Its notes keep #890's account of the declared path and of
the owed smoke. They are extended to cover every change since `ef7fadb3`, and corrected
where that history made them stale: Wrapper contract `2.33` and Skill catalog pin
`a2189cb9`. Notes are immutable once tagged, so they must describe what the tag holds.

The operator authorized this one Reopen and re-promotion. An interactive session the
operator launches may land them, but only after a full local Rehearsal of the re-promotion
has passed, every feedback loop included. Landing them creates no tag. The push that
follows #892's reviewed merge publishes the re-promotion through the unattended Promotion,
so the one-way door stays at that merge.

## Consequences

- `ef7fadb3` stays on the trunk, untagged and withdrawn.
- v0.11.3 does not carry #892's Promotion code. Its tagged `docs/releases/README.md` still
  calls the live smoke required, while its notes describe the declared path it was
  published on. The next Release carries both.
- Commits that land between the re-promotion and #892's merge are not in v0.11.3.
- A Reopen cannot repair a published target: its re-promotion is refused, because a public
  tag never moves.
- The glossary's Promotion entry, which calls the milestone the only trigger, is reworded
  with the walk change. The milestone is the only unattended trigger, an operator's
  by-exception promotion is the other route, and the same Rehearsal proves both.

## Considered and rejected

- **Tag the trunk head.** It carries no `VERSION` bump, and ADR-0059 refuses exactly that
  repair snapshot.
- **Change the `VERSION` file but not the version, for example its trailing newline.** That
  meets the letter of the bump rule and defeats its meaning.
- **Read today's declaration for `ef7fadb3`.** The declaration must travel inside the tag it
  describes (ADR-0094).
- **Let the latest declaration of a version win.** It would also clear the permanent red that
  two same-version commits cause. But a revert that restores a stable `VERSION` would then
  silently publish different content under notes written for another commit.
- **Commit a withdrawal record.** It is a second authority, with its own schema, for what the
  Reopen commit already states.
- **Publish the repaired commit by name.** That needs an attended run or a new dispatch
  trigger, and the unattended Promotion is the thing meant to publish v0.11.3.
- **Ship `ef7fadb3`'s content on a side branch.** It is the maintenance-branch shape ADR-0079
  rejected. Merged, it is invisible to the walk. Unmerged, it is refused, because
  publication refuses a candidate the trunk does not contain.
- **Reopen, then close a `v0.11.3` milestone.** The milestone job needs #892's preflight
  first. Until the milestone closes, any Integration whose issue Pickup labels `v0.12.0`
  raises the line off `0.11.3`.
- **Skip to `0.11.4`.** `ef7fadb3` would still block the walk, and six documented install
  commands already name `v0.11.3`.
- **Hand-cut the tag.** That is the exception ADR-0094 exists to retire.
