# The Continuation contract fixture

These are **not** Skills, and this is not a Skill catalog. Nothing installs this
directory, no **Run** resolves against it, and Copilot CLI does not discover it.

git-loopy reads Skills from the catalog it installs from
`git_loopy/skill_source.json` into its own config home, and from nowhere else
([ADR-0025](../../../../../docs/adr/0025-installed-skill-catalog.md)). #340
removed the repository-root tree these prompts used to sit in, because a catalog
no Run consults drifts without anything noticing — it had silently lost `tdd`
and `domain-modeling`.

What is left here is narrower: the prompts that document a request against
git-loopy's *own* `git-loopy continuation` command. A Skill is a prompt, so the
only honest way to pin what one publishes is to make its documented request
executable — the Transition-owner suites extract each fenced
`<!-- continuation-request: NAME -->` template, substitute one scenario's
durable identifiers, and drive the real native command. That is the subject
these files are kept for, and `tests/skill_templates.py` is the single extractor
that reads them.

The pinned revision does not carry these requests yet, which is why #340 moved
them instead of deleting them with the rest of the tree. When #341 publishes
them upstream and settles where a contract-carrying Skill is authored, this
fixture's replacement is a guard over the acquired revision — see
`tests/test_prompt_metadata.py` for the offline-safe shape.

Membership is a claim, and it is enforced:
`test_continuation_owner_coverage.py::test_the_contract_fixture_holds_only_contract_carrying_prompts`
fails on a prompt that carries no contract, so this cannot quietly grow back
into the catalog it replaced.
