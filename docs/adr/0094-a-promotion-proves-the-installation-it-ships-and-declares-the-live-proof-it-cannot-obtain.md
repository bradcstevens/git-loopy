# A Promotion proves the installation it ships and declares the live proof it cannot obtain

**Status:** accepted. A source-only **Promotion** runs the Rehearsal, then an
**Installation proof** of that same **Publication input**, and publishes when
the repository's committed trust policy declares the **Release smoke** an
**Unavailable proof**.

**Amends [ADR-0059](0059-verify-the-promoted-snapshot-before-publishing-an-immutable-tag.md):**
only the unconditional live-smoke condition in "Proof includes a bounded
real-host smoke", and the rejection of an advisory smoke. Tag immutability,
rehearse-before-tag, source-only as an explicit promise, publication readback
and resumable publication are untouched and remain in force.

## The required proof was never obtainable

`release-smoke.yml` has never run, not once. No repository anywhere carries the
`git-loopy-release-smoke` topic, so the disposable sandbox the smoke admits
does not exist. Of the five inputs the Promotion demands, the repository has
one: `RELEASE_PUBLICATION_TOKEN`. `release-promotion.yml` has never succeeded;
every one of its hundred most recent runs failed on the same refusal.

The refusal is not a workflow detail. `_require_passed_smoke` in
`git_loopy.release_promotion` refuses the same way with the preflight deleted,
and `release_smoke` fails closed before touching a service when its
authorization or limits are absent. The gate was real at both layers.

Meanwhile two committed statements said the opposite.
[`docs/releases/README.md`](../releases/README.md) declares source-only "so a
stable patch Release can be published with what this repository can actually
prove", and
[#873](https://github.com/bradcstevens/git-loopy/pull/873) recorded that the
operator explicitly authorized publication without the live smoke, retaining
the candidate and `source-release.yml` gates. The workflow honoured neither. So
`v0.11.0`, `v0.11.1` and `v0.11.2` were hand-cut by the operator and reconciled
by `source-release.yml` — the human act ADR-0052 deliberately declined,
reintroduced as a workaround — while `VERSION` sat at `0.11.3` with its notes
written and six documented install commands pointing at a ref that did not
exist.

A proof nobody can obtain is not a gate. It is an outage that publishes nothing
and teaches operators to go around it.

## A Promotion proves the installation it ships

The **Installation proof** is a bounded live check of the exact Publication
input, run unconditionally between the Rehearsal and publication. It installs
*that* proved archive into a clean private `uv tool` installation and requires
four things of it: the installed `git-loopy --version` is the candidate's
Release version; `commands --json` ships `init`, `runs`, `attach` and `stop`;
`init` cancelled in a real terminal saves no Config; and `init --yes` does.

It needs no credential, no sandbox repository, no agent Run and no spend. For a
source-only Release — whose entire promise is a source archive and its
committed notes — it proves the thing the Release actually hands a reader, and
closes the failure a hand-cut tag cannot see: a tag that exists while
`uv tool install git+…@vX.Y.Z` does not work.

It is not network-free. `init` refreshes the pinned Skill catalog
(ADR-0025), so an unreachable upstream makes the observation `blocked` rather
than `failed`, and a blocked proof publishes nothing. That is affordable here
in a way it would never be in an Integration feedback loop: a Promotion is
re-entered on every push to `main`, so a transient outage self-heals on the
next contribution and the tag is simply cut later. Nobody is waiting on it.

The Installation proof replaces no part of the offline matrix, and it is not an
Integration feedback loop. Like the Release smoke, its external dependency is
paid at release time.

## An unavailable proof is declared, never inferred

An **Unavailable proof** is a proof this repository states it cannot obtain,
with its reason and the issue that owes it. It is declared in
[`release-trust.json`](../../git-loopy/conformance/release-trust.json) — the
single authority for what a Release promises, which must exist in the tagged
commit with the publication worktree's copy matching. The declaration therefore
travels inside the tag: the published snapshot states which proof it carries.
It follows the shapes this repository already uses, the file's own
`unavailable_evidence` `{kind, reason}` and `fixture-claims.json`'s
`waived_owed` `{kind, reason, issue}`, and it names
[#756](https://github.com/bradcstevens/git-loopy/issues/756).

Absence is never authorization. Missing inputs must not select a weaker
promise, which is the inference ADR-0059 rejected for distribution mode and
rejects here for proof. Deleting the declaration restores the unconditional
requirement in one reviewed edit.

When the declaration stands and the five smoke inputs *are* configured, the
committed policy still wins, loudly: the Promotion publishes under the
declaration, warns that the inputs are configured while the policy declares the
smoke unavailable, and records that contradiction in its evidence. Configured
inputs do not establish that the smoke is available — no sandbox carries the
topic, and a token may lack the Copilot scope — so they do not falsify the
policy's claim, and refusing on them would fail closed against a condition that
is not an inconsistency. It would also forbid the only order in which #756 can
be paid: configure, trial by `release-smoke.yml` dispatch, then delete the
declaration.

## A smoke verdict is never synthesized

The Installation proof writes its own evidence kind with its own verdict and
its own confirmation. `confirm_smoke_evidence` is untouched and still refuses
anything that is not a full passed Release smoke, on both Execution hosts, for
this candidate's proof. No code path composes a smoke verdict from a check that
did not do the smoke's work, so no later edit can forge one by forgetting why.
The evidence carries the policy's reason verbatim, so the artifact explains
itself rather than leaving a reader to infer a waiver from an absence.

`promote_candidate` therefore publishes on a passed Release smoke, or on a
passed Installation proof together with a policy that declares the smoke
unavailable. Nothing else.

The smoke's own evidence records the Execution hosts it **observed**. It
recorded the hosts it was *asked for*, which overstates coverage; with the
scope of a check now load-bearing, that is repaired here rather than left
beside it.

## The Release says what proved it

A Release published this way states in its committed notes which proof it
carries and which is owed. `README.md` advertises both Execution hosts, so
silence would let a reader assume ADR-0059's full proof ran — the precise harm
that ADR named. `docs/releases/v0.11.3.md` already states its exception
honestly, as a one-off direct publication; it becomes a declared path with a
named debt.

## Considered and rejected

- **Provision the five inputs so live smoke really runs.** Not five switches: it
  needs a sandbox repository created and topic-marked, a token carrying
  contents, issues and Actions write plus Copilot requests, and authorized
  premium-request spend on every Promotion — to commission a machine that has
  never executed, while stable Releases stay blocked. #756 remains open and
  owed; this ADR does not retire it.
- **Keep the refusal and bless the hand-cut tag.** It is what the repository
  already does, and it is the human act ADR-0052 declined, performed under a
  workflow that reports red forever.
- **Make the live smoke advisory.** Still rejected, as in ADR-0059. An
  Unavailable proof is not advisory: it is declared in the tag, reasoned, owed
  to a named issue, surfaced on every Promotion, and it narrows the live proof
  rather than removing it.
- **Infer the declaration from the absent inputs.** The rejected shape, wearing
  a different hat: a misconfigured repository would publish a less-proved
  Release, and provisioning the variables would change the promise with nothing
  red to say so.
- **Reuse the smoke's evidence with a narrowed host list.** A `passed` smoke
  verdict for a run that proved no host is the false claim this whole decision
  refuses.
- **One evidence schema with a scope field.** One schema would mean two things,
  and every existing judgement of a smoke verdict would have to learn the
  difference.
- **A `--execution-host local` partial smoke.** It still resolves authorization,
  limits and the disposable sandbox, so it needs the token, the sandbox and real
  spend. It is not a path this repository can take, and `--execution-host`'s own
  help already says a partial smoke cannot pass a Promotion.
- **Refuse when the declaration and the configuration disagree.** See above: it
  fails closed on a condition that is not an inconsistency, and it blocks the
  only safe order for paying the debt.
- **Drop `init` from the Installation proof to keep it purely local.** It would
  never be blocked by an upstream outage, and it would stop proving that a
  freshly installed `git-loopy` can set itself up, which for a source-only
  Release is most of the user-visible promise.
