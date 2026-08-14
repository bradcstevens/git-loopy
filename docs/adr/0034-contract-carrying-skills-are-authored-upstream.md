# A contract-carrying Skill is authored upstream and mirrored here

**Status:** accepted

**Completes** [ADR-0025](0025-installed-skill-catalog.md), whose last consequence —
*"Skills that git-loopy itself depends on must exist upstream"* — was stated as a
requirement and left without a rule or a guard.

Implemented by [#341](https://github.com/bradcstevens/git-loopy/issues/341).

Some Skills carry one of git-loopy's own contracts: what they tell a session to do
includes running the native `git-loopy continuation` command. Twelve do —
`code-review`, `grill-with-docs`, `implement`, `next`, `prototype`, `push`,
`research`, `resolving-merge-conflicts`, `to-spec`, `to-tickets`, `triage` and
`wayfinder`. Eleven of them are **Transition owners** that publish a record of what
they just did; `next` is the read-only **Consumer** that reconciles those records
and writes nothing. They are the only Skills anywhere that invoke a `git-loopy`
subcommand, which is what makes them a set rather than a list.

ADR-0025 made the **installed catalog** a Run's only Skill source. Not one of those
twelve carried a single `git-loopy continuation` request at the pinned revision: the
sections that name the command lived in this repository's `.copilot/skills/`, which
#340 then removed as a catalog no Run consults. So a git-loopy *installation* had no
Skill that published Continuation guidance and none that read it back, even though
the command they would call ships in the wheel — and the Transition-owner suites
went on proving the contract coherent as git-loopy *states* it while an adopter's
session was told to publish nothing.

## Decision

### Upstream is where it is authored

A contract-carrying Skill is authored in
[`bradcstevens/git-loopy-skills`](https://github.com/bradcstevens/git-loopy-skills),
the same source of record ADR-0023 named and ADR-0025 kept. There is no second
authoring surface: the publication section is not a git-loopy-side addition to
someone else's prompt, it is part of the prompt.

A contract change therefore lands as **publish, pin, mirror**, in that order and in
one git-loopy commit: publish upstream, bump `skill_source.json` to the revision
that carries it, refresh the fixture from the acquired revision. A change that
reaches an adopter's session and a change this project's suites execute are the
same change, or the pin is what says they are not.

### This repository keeps a mirror, and only a mirror

`git-loopy/python/tests/fixtures/continuation-skills/` holds a byte-identical copy
of those twelve prompts. It exists for one reason: the suites that make a Skill's
documented request *executable* — extract its `<!-- continuation-request: NAME -->`
template, substitute one scenario's durable identifiers, and drive the real command
— must run offline, because acquisition reaches the network and a gate that reaches
the network is red for reasons no change caused.

It is a fixture, not a Skill source. Nothing installs it, no Run resolves against
it, and Copilot CLI does not discover it. It is also not an edit surface: a prompt
edited here and not upstream is the divergence this ADR exists to end, and the
guard reports it in exactly those terms.

### The guard is the pin, read both ways

`tests/test_continuation_owner_coverage.py` fails when a mirrored prompt is absent
from the acquired pinned revision, when it differs from it by a single byte, and
when the pinned revision carries a `git-loopy continuation` request the fixture has
not mirrored. The third direction matters as much as the first two: a prompt that
grows a request upstream is guidance an adopter is told to publish against a
template no suite here has ever run.

It reads a checkout and **skips** without one, the shape
`test_prompt_metadata.py::test_every_required_skill_exists_at_the_pinned_revision`
already uses. Offline it proves nothing; it is not the last line of defence, since
a Run's own Skill preflight fails closed against the installed catalog. What it
catches is drift while the prompt is being edited, which is the only moment drift
is cheap.

## Considered options

- **Author in git-loopy and sync upstream** — rejected because it inverts
  ADR-0023's source of record for one subset of prompts, leaving two authoring
  surfaces with a precedence rule to remember. It also generalises badly: the
  publication section is a *part* of the prompt, so "git-loopy owns this section
  and upstream owns the rest" is a merge conflict waiting on every upstream edit.
- **Keep no copy here and read the acquired checkout directly** — rejected because
  every Transition-owner suite would then skip in Integration. AGENTS.md already
  records why acquisition can never be a gate row, and four suites that pass by
  skipping are worse than a mirror with a mirror test.
- **Compare semantically — templates only, not bytes** — rejected because the
  prose around a template is the contract too. A reworded precondition, a dropped
  "only publish after the head is durable", a `publish` moved before the commit
  that makes it durable: each leaves the templates identical and changes what a
  session does.
- **Ship the contract-carrying Skills in the wheel again** — rejected outright.
  That is the vendored catalog ADR-0025 removed, and the reason it removed it —
  a fourth copy nothing can tell has drifted — is unchanged.

## Consequences

- An adopter who installs git-loopy now gets Skills that publish. The Continuation
  contract stops being a claim about this repository and becomes a claim about the
  pin, which is the thing an installation actually reads.
- Editing a contract-carrying prompt costs a round trip through the external
  catalog. That is the price of one source of record, and the guard makes skipping
  it fail rather than silently work.
- `bradcstevens/git-loopy-skills` now carries prompts that name a command not every
  consumer has. Each degrades to what it was: only the publication step needs
  `git-loopy`, and its README says so. `next` is the exception — its advisory
  router was replaced outright by the read-only Consumer, because two `/next`
  skills answering the same question differently is the divergence, not a hedge
  against it.
- The Transition-owner suites' closing caveat is retired. They proved coverage of
  the contract as git-loopy states it; with the mirror pinned they prove it of the
  catalog an adopter installs.
