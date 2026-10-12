# Agent profiles are Markdown beside the Skills, proved and installed with the catalog

**Status:** accepted design; implementation acceptance is tracked by the delivery issues.

**Amended by [ADR-0087](0087-the-planner-is-generated-from-the-pin-and-launched-into-the-loop-engineers-harness.md):**
the `planning` profile's body stands alone and is never layered on `PROMPT.md`, and the
profile declares `access: full`. ADR-0087 also decides how a Loop engineer's harness
gets the Planner.

Decided by [Decide how Agent profiles are expressed and proved in the Skills
repository](https://github.com/bradcstevens/git-loopy/issues/748). Builds on
[ADR-0078](0078-one-agent-profile-per-task-type.md), which put each **Agent profile**
upstream under the catalog pin, on [ADR-0025](0025-installed-skill-catalog.md) and on
[ADR-0064](0064-a-paired-skill-change-is-published-proved-then-pinned.md). Amends
ADR-0078's Permitted Skills and where Required Skills are declared.

ADR-0078 decided what each of the seven profiles is. This decision settles how a profile
is written in `bradcstevens/git-loopy-skills`, what the pin proves about it, and how an
installation carries it.

## One Markdown file per Task type, beside the Skills

The Skills repository gains a top-level `profiles/` directory holding exactly one
`<task-type>.md` per **Task type**. Its frontmatter holds the fields code reads, for
example `profiles/review.md`:

```yaml
---
required-skills:
  - code-review
permitted-skills: []
access: read-only
---
```

The body is the profile's role delta, layered on the shared Run instructions
(`PROMPT.md`). The file stem is the Task type key, and nothing else names it.

Profiles sit beside `skills/`, never inside it. git-loopy refuses any entry in the Skill
directory that is not a Skill, and the installed Skill root holds Skills and nothing else.
A profile is not a Skill either: it changes what a session preloads, exposes and may
touch, which an agent definition can do and a Skill cannot.

`access` is `read-only` or `full`, and every profile declares it. git-loopy turns it into
concrete tools and permission denials for the Copilot SDK and CLI it pins. Tool names
belong to that pin, not to the Skills pin, and "no push" cannot be written as a tool list
for a **Reviewer** that runs the feedback loops through the shell. The execution profiles
are `full`.

## What stays separate

- **The model scorecard's role map** (`skills/model-scorecard/profiles.json`) keeps only
  model-fitness data, keyed `git-loopy:<task-type>`, and git-loopy never reads it. The
  Skills repository's own validator fails when its `git-loopy:*` keys differ from the
  `profiles/` stems. The chain runs from git-loopy's closed taxonomy to `profiles/`
  (proved at pin time) to the scorecard's roles (checked in Skills CI). The scorecard's
  `skills` map, which sizes each Skill's model header, stays independent of the profiles'
  Skill lists: it says whose work a Skill is, not which Skills a role needs. Its
  `git-loopy:review` rationale still says "with push authority". It becomes "Judges
  whether work was built right, read-only: the Orchestrator alone publishes and closes
  (ADR-0072); cross-vendor review holds by construction (ADR-0048)." That edit lands
  with the first Skills-repository delivery of `profiles/`.
- **The Skills repository's `.github/agents`** — its `/next` chain carriers and its
  personas — stay that repository's interactive agents. Nothing is generated between them
  and `profiles/`, and a **Run** reads neither. `profiles/planning.md` is the **Planner**'s
  definition of record. How a **Loop engineer**'s harness gets the named Planner is a
  separate decision.

## What the pin proves

One validator judges profiles. `python -m git_loopy.skill_candidate` runs it against a
working clone, and every install and repair runs it against the pinned checkout before
anything is cut, so the pin and the installed catalog cannot disagree. It refuses:

- a `profiles/` directory that does not hold exactly one `<key>.md` per Task type in
  git-loopy's closed taxonomy, or that holds a symlink;
- frontmatter carrying any key other than `required-skills`, `permitted-skills` and
  `access`, so a model, an effort or a tool list cannot become a second authority;
- a Required or Permitted name that is not a Skill in the same checkout, because an
  unresolved preload or exposure does nothing and reports nothing;
- a Skill named twice, or both Required and Permitted, in one profile;
- an `access` other than `read-only` or `full`, and a `review` profile that is not
  `read-only` or does not require `/code-review` (ADR-0072);
- an empty role delta.

The profiles take over the Required Skill contract the candidate check reads from
`PROMPT.md` today.

## How an installation carries profiles

The install cuts a sibling root, `<config-home>/git-loopy/profiles/`, from the same proven
checkout and in the same wholesale replacement as the Skill root. The pin names the profile
directory beside the Skill directory. The install record moves to schema 2 and records a
digest of each root. The installed catalog is `matching` only when both match, so a profile
edited in place is drift, and the next refresh or `doctor --apply` repairs it the way it
repairs a Skill. A remote **Lane**'s job installs the same pinned revision and reads the
same root.

The installed profile root is the only profile source a Run reads. No repository or
personal source overrides it. A project customizes through its Run instructions and its
**Skill policy**.

## Amendments to ADR-0078

- `/resolving-merge-conflicts` is Permitted on the five execution profiles. Today every
  Run may invoke it, and Pull-request mode sends an Agent to it on a diverged branch. The
  ADR-0078 table had left it unreachable.
- Required Skills come only from the profiles. A Run's Required Skills, and the **Minimal
  Skill policy**, are the union of the in-Run profiles' Required Skills: {`code-review`,
  `tdd`, `diagnosing-bugs`}. `required-skills` in custom Run instructions is retired.
  Preflight, and `doctor` through the same seam (ADR-0055), refuse a custom `PROMPT.md`
  that still declares it and name the key to remove.

## Delivery order

The Skills repository publishes `profiles/` first. The current pin validates only the
Skill directory, so the new directory changes nothing for installations behind it.
git-loopy then ships the validator, the installer, the Run's use of the profiles and the
pin bump as one paired change: published, proved, then pinned (ADR-0064).

## Considered options

- **Extending the scorecard's role map.** Rejected: it is one Skill's private resource,
  it holds model-fitness data a profile must not carry, and Skill internals move
  (`model-fit` became `set-task-models`).
- **`.agent.md` files.** Rejected: a git-loopy session never offers file agents
  ([#655](https://github.com/bradcstevens/git-loopy/issues/655)), the format documents no
  `skills` key and nothing that means Permitted, and an installed file agent would run a
  role delta without the Run instructions or Routing.
- **One manifest, with prompts in sibling files.** Rejected: two files per profile to keep
  in step, for no field the frontmatter cannot hold.
- **A literal tool allowlist, or access derived only in code.** Rejected: an allowlist
  names tools the Skills pin does not own and still cannot say "no push". Access derived
  only in code leaves the profile file an incomplete description of the profile.
- **Reading profiles from the private checkout.** Rejected: an in-place edit there is
  invisible to the install record, so it could never be repaired.
- **A project override.** Rejected: it reopens the closed, proved set the pin exists to
  guarantee.
