# A self-initiated commit is a Checkpoint by construction, not checked at runtime

**Status:** accepted design; implementation acceptance is tracked by
[#783](https://github.com/bradcstevens/git-loopy/issues/783).

Decided by [Decide how §A's Checkpoint trailer rule is enforced, if at
all](https://github.com/bradcstevens/git-loopy/issues/470). Resolves the open question
[ADR-0050](0050-a-hosts-terminal-failure-is-classified.md) leaves at its end. Builds on
[ADR-0004](0004-runner-checkpoint-and-push-durability.md) and
[ADR-0068](0068-execution-hosts-own-contributions-clients-own-terminals.md).

§A of spec [#445](https://github.com/bradcstevens/git-loopy/issues/445) allows a host one
commit of its own: a commit that cleans its tree MUST be a **Checkpoint**, with the
Checkpoint trailer and no closing keyword. ADR-0050 found this unenforceable at the seam as
stated. git-loopy cannot tell a host-authored commit from an agent-authored one, and
rejecting any closing keyword fails because the Run instructions require the agent to close
its own issue.

The tickets that rule waited on have since shipped: the seam (#447), the fetch by completion
SHA (#451), salvage (#452) and the GitHub Actions host (#460). The premise still holds:

- **No authorship discriminator exists.** The Actions workflow commits as `github-actions[bot]`
  for the agent and the host alike, and the local host commits as the operator. The
  `GitLoopy-Checkpoint` trailer is the only marker, and whoever writes the message can stamp it.
- **Every self-initiated commit already goes through one helper.** The Iteration Checkpoint
  and per-Lane sites in `loop.py`, salvage in `sweep.py`, and `checkpoint_dirty_tree` in
  `github_actions_worker.py` all build their message with `wrapper.checkpoint_message`. That
  helper always carries the trailer and never matches `CLOSE_KEYWORD_RE`.
- **Every host is code git-loopy ships.** Execution hosts exist only in the Python Runner;
  the shell and PowerShell members reference hosts only in Event conformance.

## One rule, held by construction

Every commit git-loopy authors on its own initiative is a Checkpoint built by
`checkpoint_message`: a host cleaning its tree, the Orchestrator's Checkpoint
([wrapper contract §7](../wrapper-contract.md)) and salvage. §A's host rule and #452's salvage
rule are this one rule, not two that happen to agree.

It is an **obligation on implementations, with no runtime check**:

- **No authorship discriminator is invented.** A marker the host reports about itself can be
  forged by an agent writing the same trailer. A pinned committer identity would reconfigure
  the Actions workflow and the operator's local identity only to police git-loopy's own code.
- **No validation is added at materialization.** `contribution_materialization.py` keeps its
  ref and SHA rejections and does not read commit messages.
- **No disposition is added.** No violation is detected, so none is charged. In particular,
  a host's fault is never charged to the issue as a `breach`.

## Pinned by a test, not by prose

#447 shipped this rule as docstring prose with nothing behind it, which is how the question
stayed open. The construction is pinned instead:

- A structural test in the Python suite fails when any self-initiated commit site in
  `git_loopy` builds its message without `checkpoint_message`. It must also catch a site
  added later, not only today's sites.
- A test asserts that `checkpoint_message` always carries the trailer and no closing keyword.
- The `ExecutionHost` docstring states the obligation for anyone implementing a host.

## What reopens enforcement

The first Execution host whose commit-making code git-loopy does not ship, such as a
third-party implementation of the seam, ends "by construction". That host's ticket must
reopen the questions #470 set aside: what carries authorship, where the returned branch is
validated, and what disposition a violation takes, since a host's fault is not the issue's.

## Consequences

- **The wrapper contract is unchanged.** §7 already binds every Checkpoint, and only the
  Python Runner has hosts, so the Contract version (2.26) does not move and no other member
  is affected.
- **No glossary entry changes.** **Checkpoint** already names this commit. The rule's
  enforcement is a property of the code, not a new term.
- **ADR-0050 stands.** This ADR answers the question ADR-0050 deferred and changes none of its
  classes.
