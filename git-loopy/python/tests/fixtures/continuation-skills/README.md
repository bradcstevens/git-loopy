# The Continuation contract fixture

These are **not** Skills, and this is not a Skill catalog. Nothing installs this
directory, no **Run** resolves against it, and Copilot CLI does not discover it.

git-loopy reads Skills from the catalog it installs from
`git_loopy/skill_source.json` into its own config home, and from nowhere else
([ADR-0025](../../../../../docs/adr/0025-installed-skill-catalog.md)). #340
removed the repository-root tree these prompts used to sit in, because a catalog
no Run consults drifts without anything noticing — it had silently lost `tdd` and
`domain-modeling`.

What is here is a **mirror**, byte for byte, of the twelve prompts at the pinned
revision that document a request against git-loopy's own `git-loopy continuation`
command. It exists so the Transition-owner suites can run offline: a Skill is a
prompt, and the only honest way to pin what one publishes is to make its documented
request executable — the suites extract each fenced
`<!-- continuation-request: NAME -->` template, substitute one scenario's durable
identifiers, and drive the real native command. Acquiring the pin reaches the
network, and a gate that reaches the network is red for reasons no change caused,
so the suites read this copy instead. `tests/skill_templates.py` is the single
extractor that reads them.

**This is not an edit surface.** A contract-carrying Skill is authored in
[`bradcstevens/git-loopy-skills`](https://github.com/bradcstevens/git-loopy-skills)
and reaches git-loopy as publish, pin, mirror — in that order, in one commit
([ADR-0034](../../../../../docs/adr/0034-contract-carrying-skills-are-authored-upstream.md)).
Edit a prompt here and you have made the change an adopter's session will never
see.

Membership is a claim, and it is enforced from both sides.
`test_continuation_owner_coverage.py` fails on a prompt that carries no contract —
so this cannot quietly grow back into the catalog ADR-0025 retired — and on any
prompt that is absent from, differs from, or is present upstream and unmirrored
here. The pin is what settles every one of those disagreements.
