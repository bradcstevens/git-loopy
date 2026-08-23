# A doctor shares the Run's preflight seam

**Status:** accepted

Settles what `git-loopy doctor` is allowed to check. A diagnostic that maintains its own list of
checks will drift from the checks a Run actually performs, and the drift has a direction: `doctor`
goes green, the Run dies anyway. That failure costs more than the missing diagnostic did, because an
operator who is cleared and then fails stops trusting the tool after one occurrence.

`doctor` therefore calls the same resolution a Run calls and reports what it says. It does not
maintain a parallel check list.

## What was already true

**A Run aborts at Skill-policy preflight, after the operator has committed to it.**
`skill_policy.py:109,257` raises `MissingEnabledSkills` when the persisted `enabled_skills` names a
Skill absent from the pinned catalog; `loop.py:4302-4304` catches it and ends the Run. Nothing
detects this beforehand, so the first signal is a Run that will not start.

**`info` and `doctor` read the same facts.** Install channel, Release version, config scopes,
Skill-catalog revision, helper version, `gh` auth — both commands want all of it. Left unpinned,
this produces two commands nobody can choose between.

## The decisions

**`info` describes; `doctor` judges.** `info` prints facts, carries no verdict, always exits 0, and
gets `--json`, because a stable fact dump is the thing worth scripting. `doctor` runs only the checks
that can fail, prints a verdict, and exits non-zero when one does — which is what makes it usable in
CI and pasteable into a bug report.

**`doctor` shares the Run's preflight seam.** It invokes the same Skill-policy resolution, roster
resolution, and Config resolution the Run invokes. It is not permitted to reimplement them.

**The Run grows the environment checks, rather than `doctor` growing them alone.** Sharing the seam
would otherwise cap `doctor` at whatever the Run already checks, leaving out the things an operator
most wants verified: `copilot` on `PATH`, `gh` authenticated, and `AGENTS.md` declaring a runnable
loop. These move into the Run's preflight, where both commands get them.

That last one is worth its cost independently of `doctor`. `AGENTS.md` states that *"a repository
that declares no runnable loop here cannot be gated at all, and every Lane merge comes back red
regardless of what it contains."* Discovering that at Integration rather than at Run start burns a
Run to learn something knowable in advance.

## Consequences

`doctor` can never check something the Run does not. That is the constraint, not a limitation to be
worked around later: the moment an exception is made, the drift this ADR exists to prevent begins.
A check worth having is a check the Run should be performing.

Moving environment checks into Run preflight changes when a Run fails — earlier, and for reasons it
previously discovered late or not at all. This is Python-Runner-only for the reasons given in
ADR-0054, and enters no Conformance fixture.
