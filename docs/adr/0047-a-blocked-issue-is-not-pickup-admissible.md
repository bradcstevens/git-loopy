# A blocked issue is not Pickup-admissible

**Status:** accepted

Decided by [#437](https://github.com/bradcstevens/git-loopy/issues/437).

A candidate whose native tracker dependencies include an open issue is **not admissible at
Pickup**. The runner walks past it and binds the next candidate in §3.2's order. It stays in the
**Pool**, it costs no **Strike**, and it becomes admissible again the moment its last blocker
closes, with no human touching the issue.

Today no member of the **Runner family** reads native dependencies at all ([#436]). A blocked
issue is bound, rendered into a prompt, and given a whole agent session to discover from prose
what the tracker already knew. This ADR is the decision the four members — the Python reference
**Orchestrator**, the shell and PowerShell ports, and the Rust **Dashboard** — are then held to,
landed one slice ahead of them so they implement one decision rather than four compatible
guesses. No member's behaviour changes here.

[#436]: https://github.com/bradcstevens/git-loopy/issues/436

## Readiness is not eligibility

**Eligibility** is how a human authored the issue: the `ready-for-agent` label and the AFK-ready
discriminator's two headings. Only a human can change it, and §3.1 settles it at collection.

**Readiness** is a fact about the tracker's dependency graph. It clears itself. Nobody edits the
issue; a blocker closes somewhere and the candidate is admissible on the next **Iteration**.

Because they are different in kind, they belong at different moments. Eligibility is decided once,
at collection, from the cheap list read. Readiness is decided at **Pickup**, per candidate, and
only for the candidates the runner actually reaches — walking §3.2's order front to back and
stopping at the first admissible one. That is one dependency read per candidate considered, not
one per candidate collected.

§3.3 said admission "MUST NOT be widened past that into re-deciding eligibility". That prose was
already stale when this was decided: the serial `admit` refuses on the **Attempt lifecycle**
([#412]) — a candidate this Run has already given up on — which is not a routing refusal and not a
re-decision of eligibility. The contract described a closed admissible set that had already
acquired a second member. §3.3 now names the set as **open**, and names what may join it: a
refusal is admissible when it is a fact the runner must resolve *at Pickup* in order to start the
session, and when it is not a second opinion on something a human already asserted.

[#412]: https://github.com/bradcstevens/git-loopy/issues/412

## Blocked is a Pickup skip, not a Pool exclusion

A **Pool exclusion** is an authoring mistake, reported so a human fixes it. A blocked candidate is
not a mistake — it is correctly authored work whose turn has not come.

The distinction is structural, not cosmetic. A blocked candidate MUST stay in the **Pool**: the
closure whitelist, the collection Event and the emptiness test all still need to see it. A **Pool**
that is empty ends the Run cleanly; a Pool whose candidates are merely waiting has not run out of
work. Excluding a blocked issue would make those two indistinguishable.

§3.1 also forbids the alternative on cost. Exclusions "MUST be reported as `wrapper.pool.excluded`
Events, **before** the `wrapper.afk_ready.collected` they explain", and "MUST NOT cost an extra
source round-trip: the cheap list read already carries the body the decision is made on." A
`blockedBy` read is not in the cheap list read. Deciding readiness at collection would cost one
extra read for **every** `ready-for-agent` candidate, where deciding it at Pickup costs one for
each candidate actually considered — and on the common path, that is one.

## It charges no Strike

A **Strike** counts issues this Run gave up on. A blocked candidate was never attempted: no
session was spent, no pair was burned, nothing was learned that a retry would repeat. It costs
nothing and is reconsidered from scratch on the next Iteration.

## Reading a blocker is not inference

[#219]'s non-goal — "eligibility is never inferred from issue content, dependencies, or code
overlap" — governs **Parallel-safe**, which asks whether two issues may safely land beside each
other. That question has no ground truth in the tracker, which is exactly why inferring it was
ruled out and why `parallel-safe` is a label a human applies.

A native `blocked_by` edge is not an inference from that class. It is a human assertion, carried
by a different tracker affordance than a label, and read literally. The runner does not decide
that #500 blocks #501; a person said so, in a field built to say it. Reading it is the same act as
reading `ready-for-agent`.

The boundary is traversal. The runner reads **one hop** — the candidate's own `blockedBy`
connection — and stops. It does not walk the graph transitively.

[#219]: https://github.com/bradcstevens/git-loopy/issues/219

## One hop, never traversed (non-goal)

Transitive traversal is a **non-goal**, stated here so it is refused deliberately rather than
added later by someone who thinks it was an oversight.

The moment the runner walks the graph, it is computing a property *of the graph* rather than
reading an assertion *about an issue* — which lands it back inside the class of inference #219
ruled out, having arrived there one edge at a time. It is also unbounded: GitHub caps `blockedBy`
at 50 links per issue, but says nothing about depth.

This settles cycles without a cycle rule. A **self-edge** (#500 blocked by #500) is visible for
free — the candidate's own number is already in hand — and gets the ordinary verdict: it has an
open blocker, so it is **Blocked**. It is pinned in the fixture explicitly, so three ports agree
rather than one treating "blocked by self" as vacuously ready and another failing on it. A cycle
of length two or more is, at one hop, indistinguishable from an ordinary blocker, so there is no
rule to state and no traversal is bought to detect one.

The cost is accepted knowingly: an unsatisfiable dependency graph reads to the operator as
"waiting on blockers", which is true and unhelpful. It is a tracker defect, and the tracker is
where it is visible.

## A blocker in another repository blocks

GitHub supports cross-repository `blocked_by` edges. Such an edge counts **identically** to a
same-repository one, and the skip reason carries the full `owner/repo#number`.

The premise of this ADR is that a `blocked_by` edge is a human assertion. A person writing
"blocked by `other-repo#12`" asserted exactly what they assert with "#12"; the words do not weaken
by crossing a repository boundary. Letting the verdict turn on *where the blocker lives* would
make readiness a fact about git-loopy's convenience rather than about what the human said.

The consequence is accepted: such a blocker can never be closed by this Run, and a Pool blocked
entirely by cross-repository edges is a Run waiting on work no Run here can do. That is a true
report of the situation, and the operator is told which repository to go to.

## An unprovable read is not readiness

`blockedBy` is a paginated connection: a candidate can report a `totalCount` greater than the
nodes returned, and a node can come back unreadable. **Readiness that cannot be proven is not
readiness** — the candidate is skipped.

It is skipped under its **own** reason, `readiness_unprovable`, not under
`blocked_by_open_dependency`. The two are different facts. `blocked_by_open_dependency` reports an
assertion that was read: an open blocker exists, and here it is. `readiness_unprovable` reports
that no assertion could be read at all — there may be no blocker whatsoever. Collapsing them would
have the runner assert the very thing it just failed to establish, and would tell an operator to
wait for a blocker to close when the truth is that the read did not complete. The first wait ends;
the second never does.

GitHub caps `blockedBy` at 50 links per issue and GraphQL accepts `first: 100`, so a complete read
is always **one page**. An incomplete read therefore never means "the list is long". It means the
member asked for fewer than the cap — a member defect, and the contract requires asking for at
least the cap — or the read partially failed, which after the cross-repository decision above is
routine rather than exotic: a blocker in a repository the token cannot see returns its count
without its node.

## The read is GraphQL

The contract pins **GraphQL** as the dependency read. This is the first GraphQL call in git-loopy;
every existing `gh` invocation in `gh.py` is REST.

It is pinned rather than left to each member because REST is documented to **undercount**
cross-repository dependencies, and it undercounts silently — there is no `totalCount` to notice
the shortfall by. A REST implementation would report a blocked issue as **ready**, which is the
exact failure this ADR exists to prevent, and it would do it invisibly. Leaving the mechanism to
the members would mean four members with four different truths about the same issue.

## Considered and rejected

- **Decide readiness at collection, as a Pool exclusion.** One decision point instead of two, and
  it reuses machinery that already exists. Rejected because it is the wrong meaning and the wrong
  price: a blocked issue is not an authoring mistake, an excluded candidate leaves the Pool that
  the closure whitelist and the emptiness test still need it in, and §3.1 forbids the extra
  round-trip it would cost on every candidate rather than on the ones considered.

- **Leave it to the agent, as `PROMPT.md` does today.** Costs nothing and already "works": the
  agent reads the issue, notices the dependency, and stops. Rejected because it spends a whole
  session — a model pair, a checkpoint, a push — to rediscover a fact the tracker holds in a
  structured field, and because what the agent does about it is unobservable: the runner sees an
  Iteration that did no work and cannot tell it from a failure.

- **Walk the dependency graph transitively** so real cycles and deep blockers are detected.
  Rejected under "One hop, never traversed": unbounded reads, and it converts reading an assertion
  into computing a property of a graph.

- **Ignore cross-repository blockers** as out of scope for a repo-scoped tool. Tidy, and it
  guarantees every blocker this Run honours is one this Run could close. Rejected because it makes
  the verdict depend on where the blocker lives rather than on what the human asserted.

- **One reason string for both blocked and unprovable.** Keeps the vocabulary at one term and an
  operator arguably acts the same on either. Rejected because they are different facts and only
  one of them is a blocker; see "An unprovable read is not readiness".

## Vocabulary reclaimed from ADR-0046

`CONTEXT.md` gains **Readiness** and **Blocked** — two names [ADR-0046](0046-continuation-is-decommissioned.md)
deleted the same day, when Workflow Continuation was decommissioned and took some seventy terms
with it, `Blocker` and `Readiness` among them.

The names are reused deliberately. ADR-0046 declined to design a replacement so that the next
mechanism would be "reached from the problem, not inherited from this one's vocabulary" — that
governs mechanism design, not the English language. These are the plain words for the plain
concepts, they are unoccupied, and coining synonyms to avoid a deleted term is how a glossary
rots. The senses are unrelated: nothing here concerns Workstreams, Anchors or Producers, and a
reader who finds ADR-0046 removing `Readiness` and `CONTEXT.md` defining it should find this
paragraph.

## Consequences

- **Nothing changes yet.** The Python, shell and PowerShell suites are green unchanged. The
  decision, the vocabulary, the contract amendment and the Conformance fixture land together;
  [#438]–[#443] implement them.
- **A new obligation is unclaimed by three members.** `issue-readiness.json` is loaded by the
  Python conformance suite. The shell port, the PowerShell port and the Dashboard do not load it
  and will not fail for ignoring it — the Conformance suite has no completeness check, which is
  how `attempt-lifecycle.json` is claimed by Python alone and passes green today. That hole
  predates this decision and is not closed here.
- **§3.3's admissible set is now open**, and governs **Lane** pickup as well as serial Pickup, so
  [#439] implements a stated rule rather than inventing one.
- **git-loopy acquires a GraphQL dependency.** `gh api graphql` joins the REST calls in `gh.py`.
- **A Run may end with a Pool nobody can start.** Every candidate blocked is not an All-skipped
  Run and must not read as one; [#443] settles what it reads as.

[#438]: https://github.com/bradcstevens/git-loopy/issues/438
[#439]: https://github.com/bradcstevens/git-loopy/issues/439
[#443]: https://github.com/bradcstevens/git-loopy/issues/443
