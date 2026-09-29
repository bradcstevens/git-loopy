# A candidate awaiting a pull-request merge is not Pickup-admissible

**Status:** accepted

Amends [ADR-0047](0047-a-blocked-issue-is-not-pickup-admissible.md). It keeps that decision's
one-hop read, its GraphQL transport, its cross-repository rule, its `readiness_unprovable` reason
and its Pickup-skip shape. It widens what **Readiness** means, and adds a second refusal to it.

Decided by [#692](https://github.com/bradcstevens/git-loopy/issues/692), the decision slice of
Spec [#690](https://github.com/bradcstevens/git-loopy/issues/690).

A candidate that an open pull request will close when merged is **Awaiting merge**, and it is
**not admissible at Pickup**. The runner walks past it and binds the next candidate in §3.2's
order. It stays in the **Pool** and costs no **Strike**, and it clears itself with no human
touching the issue: when the pull request merges, the issue closes and leaves the Pool, and when
the pull request closes unmerged, the candidate is admissible again on the next **Iteration**.

No member of the **Runner family** reads closing pull requests today. This ADR is the decision
they are then held to, landed one slice ahead of them as ADR-0047 was, so they implement one
decision rather than four compatible guesses. The three Orchestrators are held to the admission
decision through `awaiting-merge.json`. The Rust **Dashboard** decides no admission, so it records
a **Permanent waiver** on that fixture, and is held instead to the skip reason this decision
names, through the **Unbound-Run notice** ([#694]). No member's behaviour changes here.

## What happened

On 2026-09-26 two Runs, `01M3FDXCJSH2NBP508VM8FYYA1` and `01M3G8DFQRCM4T7TBNF02YJ54D`, each
exited having done nothing. #679's implementation had already been published on [#688]
(`Closes #679`) for human review, but #679 kept `ready-for-agent`, so every Run bound it. The
agent confirmed that the work was on the pull request and made no progress, and the **Attempt
lifecycle** defeated the issue. Each Run paid one or two sessions (10.5 and 17.1 AI credits) and
a **Strike** for that.

Seven candidates, #680–#686, were **Blocked** behind #679. With #679 defeated, the Run found
nothing it could bind and ended `all_skipped`, which means "a labelling mistake an operator can
fix". The operator's actual next act was to merge #688, and nothing named it.

The tracker already held the fact. GitHub records the pull request that will close an issue in a
structured field, just as it records a native `blocked_by` edge. ADR-0047 rejected leaving
blockers to the agent for two reasons. It spends a whole session to rediscover a fact the tracker
holds in a structured field. And the runner then sees an Iteration that did no work, and cannot
tell it from a failure. Both reasons hold here unchanged. The gap recurs whenever work is
published for human review outside a Run: `/push`'s step 8, a human's own pull request, or
another agent's.

[#688]: https://github.com/bradcstevens/git-loopy/pull/688

## Readiness widens

ADR-0047 made **Readiness** a fact about the tracker's dependency graph. It now means that
**nothing outside the Run stands between the candidate and its session**: no open `blocked_by`
dependency, and no open pull request that will close it. Both are facts the tracker holds about
the issue rather than about how the issue was authored. Both are read literally, and both clear
themselves with no human touching the issue.

Awaiting merge joins §3.3's admissible set on ADR-0047's own test. A refusal may join when it is
a fact the runner must resolve *at Pickup* in order to start the session, and when it is not a
second opinion on something a human already asserted.

- **It must be resolved at Pickup.** Whether a session is worth starting depends on it, and it
  changes between Iterations without anyone editing the issue, so it cannot be settled once at
  collection the way **Eligibility** is.
- **It is not a second opinion.** `ready-for-agent` asserts that the issue is authored for an
  agent. It does not assert that nobody has done the work. A closing reference is a separate
  assertion: a person or an agent wrote `Closes #679`, or linked the pull request, in a field
  built to say it. Reading it is the same act as reading `blocked_by`.

## Awaiting merge is not Blocked

It is skipped under its **own** reason, `awaiting_pull_request_merge`, not under
`blocked_by_open_dependency`. A closing pull request is not a dependency. It is the candidate's
own work, already done and waiting on a merge. The two facts also ask different acts of the
operator. A Blocked candidate waits on other work, and an Awaiting-merge one waits on a review of
the pull request it names. Collapsing them would tell an operator to finish a dependency that
does not exist.

The reason carries the verdict. No new verdict literal is added: `issue-readiness.json`'s
verdicts stay `ready` and `blocked`, and that fixture is unchanged.

## Only a pull request read as open refuses

A pull request read as `OPEN` refuses, a draft included. A draft reports `OPEN`, so whether it is
a draft is never read. A draft is published work waiting on a human as much as a ready pull
request is, and a session would redo it just the same.

`MERGED` and `CLOSED` never refuse. A merged pull request can leave its issue open: its work has
landed, and what the open issue still asks for is new work. A pull request closed without merging
will never close the issue.

So appearing in the connection proves nothing on its own, as two observations on `gh` 2.96.0
show:

- **The default connection mixes states.** `closedByPullRequestsReferences` omits pull requests
  closed without merging, but keeps merged ones. #640's connection lists merged #669, while
  #432's lists closed #617 only under `includeClosedPrs`.
- **gh projects no state.** A projected reference carries its id, number, url and repository,
  and nothing else. gh's source at 2.94.0, the Runner's floor, projects the same.

The state is therefore a second read.

## The references ride existing reads; their states cost a read

`gh issue view` and `gh issue list` both serve `--json closedByPullRequestsReferences` from
GraphQL, so the references ride every read that already carries `blockedBy`, at no extra cost.
Their states do not ride, because gh projects none. Each read that carries references resolves
their states itself, with a GraphQL `nodes(ids:)` request over their distinct ids.

That is the one cost this decision adds. It is paid **per read, never per candidate**, which
keeps ADR-0047's rule that Readiness adds no per-candidate round-trip.

**One hop** holds unchanged. A pull request's checks, reviews, mergeability and own references
are never read. A closing pull request in another repository refuses exactly as one in this
repository does, and it is named by its full `owner/repo#number`.

## The four open questions

#690 left four questions open, and the operator settled them on #692. `awaiting-merge.json`
records each answer and names the cases that pin it.

**More than 100 references.** One `nodes(ids:)` request resolves at most 100 ids, so a read
carrying `d` distinct references makes `ceil(d / 100)` state requests. Cost grows with the
distinct references a read carries, never with its candidates. A pull request several candidates
share is resolved once, and a read carrying none makes no request. "At most one extra request per
read" is the case of up to 100 distinct references. A failed request leaves unread only the ids
it carried, so a candidate whose pull request another request read as open is still Awaiting
merge.

**Completeness of the connection itself.** gh projects neither `totalCount` nor `pageInfo` for
this connection, so the counterpart of `blockedBy`'s completeness rule is decided by the
**carrier**.

- **A view-carried connection is complete at any length.** `gh issue view` pages the connection
  a hundred nodes at a time until `hasNextPage` is false.
- **A list-carried connection is complete below 100 nodes.** `gh issue list` pages only the
  Issues connection, and asks each issue for one page of a hundred references. So a list-carried
  connection of exactly 100 nodes is **incomplete**, because it may have been cut short.
- **A zero-valued node is unreadable.** A node gh rendered from `null` arrives as its zero value:
  an empty id and number `0`.

Each of the following leaves Readiness unproven — `readiness_unprovable`, never admitted:

- an incomplete connection;
- an unreadable node;
- a failed state request;
- a state request that returned `null` for an id, because the token cannot see the pull request.

**The floor is [#695]'s to confirm.** At the floor, `MIN_GH_VERSION_FOR_READINESS` (2.94.0), both
carriers were read from gh's source only. [#695] owns confirming that the floor serves the field
on both reads and pages it on `gh issue view`.

**The authoritative re-reads.** Every read that decides Readiness resolves its own states:

- the §3.1 collection read;
- each **Membership read**;
- each authoritative re-read of one candidate. These are a **Lane**'s own Pickup validation, and
  the re-read a candidate gets when its **Routing preparation** starts.

A re-read of one candidate is view-carried, so its connection is complete. Its states cost one
request when the candidate carries a reference, and none when it carries none. No verdict is
taken from states another read resolved, so a merge between two reads is seen by the second. The
serial Pickup walk and Lane candidacy issue no read of their own. They take their verdicts from
the read that carried them, exactly as they do for `blockedBy`.

**An unprovable blocker read beside a pull request read as open.** The candidate is Awaiting
merge, its reason names that pull request, and it counts as a wait in the unbound-Pool rule.
Whatever the unread blockers turn out to be, the candidate cannot be admitted while the pull
request is open. So the truthful report is the wait an operator can end by merging it, not a read
an operator must repair. The unread blockers matter only if the pull request closes unmerged, and
the next read reports them then.

## Precedence

A candidate reports the first of `blocked_by_open_dependency`, `awaiting_pull_request_merge` and
`readiness_unprovable` that its reads establish. It is admissible only when they establish none.

**Blocked outranks Awaiting merge.** Both are waits, so the order changes no unbound-Pool class;
it decides only what the reason names. With Blocked first, every candidate Blocked before this
decision reports exactly the reason it reported, and a candidate carrying both names its blockers
only. A human asserted that such a candidate waits on its blockers, so the blockers are the
earlier act.

**Both proven waits outrank `readiness_unprovable`**, in either connection. A blocker read as open
outranks an unread pull-request state. A pull request read as open outranks an unprovable
`blockedBy` read, an incomplete connection, an unreadable reference node, and an unread state for
another reference. A fact that was read is never displaced by one that could not be.

## The reason names what to merge

The reason is `awaiting_pull_request_merge: <owner/repo#N>[, <owner/repo#N>...]`. It names every
closing pull request read as open by its full reference, in connection order, the way
`blocked_by_open_dependency` names blockers.

The reason string carries the references, so `wrapper.pickup.skipped` needs **no new Event
field**, and `event_schema_version` does not move. How the **Dashboard** renders the reason, and
how the **Unbound-Run notice** names the pull requests to merge, land with [#694].

## The unbound-Pool rule counts it as a wait

`awaiting_pull_request_merge` counts as a wait beside `blocked_by_open_dependency`. A Pool that
bound nothing ends:

- `preflight_failed` when any refusal was `readiness_unprovable`, as before;
- otherwise `all_blocked` when every refusal was a wait, in any mix of the two;
- otherwise `all_skipped`, as when a candidate was defeated by the **Attempt lifecycle**.

Replay the minimised repro of the incident against this rule: an Awaiting-merge head, and a
candidate Blocked by it. The Run spends no session, charges no Strike, and ends `all_blocked`.

## The Pin, Rolling dispatch and pull-request candidates

- **The Pin.** An Awaiting-merge **Pin** is an answer about the Pin itself, so it is passed over
  and **spent**, exactly as a Pin with an open `blocked_by` dependency is. A Pin whose Readiness
  is unproven has no answer, so it stays live.
- **Rolling dispatch.** Lane candidacy refuses an Awaiting-merge candidate exactly as it refuses a
  Blocked one: no reservation, no **Pickup skip**, and no eviction. The next Membership read is
  the whole of what promotes it once the pull request closes unmerged. A Rolling terminal
  decision's `refusals` carries each such candidate with its full reason.
- **Pull-request candidates.** A pull-request candidate in PR mode is never Awaiting merge. A
  member reads no closing references for it and resolves no state, just as `blockedBy` does not
  apply to it.

## A new fixture, not new cases

#690's Seam 1 put the new cases in `issue-readiness.json` and `exit-codes.json`. This decision
lands them in a fixture of their own, `awaiting-merge.json`, and leaves both of those exactly as
they are.

Python, shell and PowerShell already claim both fixtures, and a claiming suite runs every case of
its fixture (§13.1). Appending cases no member implements yet would turn every claiming suite red
the moment they landed. ADR-0047's fixture could land a slice ahead of its implementations only
because it was new, and no suite read it yet.

- **Each Orchestrator records an Owed waiver** on `awaiting-merge.json`, tracked by its own
  ticket: Python [#695], shell [#697], PowerShell [#698]. That ticket turns the waiver into a
  **Fixture claim**.
- **The Dashboard records a Permanent waiver**, as it does for `issue-readiness.json`. Readiness
  is an Orchestrator admission decision, and the Dashboard never reads an issue's tracker state.

`exit-codes.json`'s `unbound_pool_cases` count refusals, and a count cannot tell an Awaiting-merge
refusal from a Blocked one. So the new fixture restates the unbound-Pool rule by refusal reason.

## Contract 2.17

The Wrapper contract states this decision at 2.17. That was the next free minor, because
concurrent work had already claimed 2.15 and 2.16. The advance moves the contract header and the
Python `WRAPPER_CONTRACT_VERSION`, and the new fixture declares it.

It adds no Event field and changes no fixture the Dashboard reads. So `event-schema.json` and
`dashboard-insights.json` keep their version pins, and so does the Dashboard core's
`WRAPPER_CONTRACT_VERSION`, which names the version whose Dashboard seam it implements. Contract
2.13 set that precedent: it moved the contract header and the Python constant, and left the
Dashboard pins where they were.

## Considered and rejected

- **A distinct `all_awaiting` Run outcome.** It would tell the two waits apart at the Run's end.
  Rejected because it adds an exit reason that the family, the Dashboard and supervising processes
  would all have to learn. The operator's class of act is the same as `all_blocked`'s: act, or
  wait, outside the Run. Each refusal's reason already tells the two waits apart.
- **Admit on an unreadable state, as today.** It costs nothing, and a failed state read would
  never stop work. Rejected because it contradicts ADR-0047's "an unprovable read is not
  readiness", and a member that silently admits looks exactly like today's wasted session.
- **A Pool exclusion.** One decision point at collection, reusing machinery that already exists.
  Rejected because it is the wrong meaning. The candidate is correctly authored work, and it must
  stay in the Pool, which the closure whitelist and the emptiness test still read.
- **Leave it to the agent, as today.** It already "works": the agent reads the issue, finds the
  pull request and stops. Rejected because that is the incident: a whole session and a Strike
  spent to rediscover a structured fact, in an Iteration the runner cannot tell from a failure.
- **Take the verdict from states an earlier read resolved.** The collection read has already
  resolved them, so a re-read could skip its own state request. Rejected under
  "The authoritative re-reads": a merge between the two reads would go unseen, and the verdict
  would rest on a state its own read never made.
- **Report an unprovable blocker read beside an open pull request as unprovable.** It would keep
  `readiness_unprovable` absolute. Rejected under
  "An unprovable blocker read beside a pull request read as open": it displaces a fact that was
  read with one that could not be, and it asks the operator to repair a read when the act that
  ends the wait is a merge.
- **Rank Awaiting merge above Blocked.** It would name a pull request the operator could merge
  now. Rejected because it would change the reason of every Blocked candidate that also carries a
  pull request, and a human asserted that such a candidate waits on its blockers first.

## Out of scope

- **Relabelling issues.** The Runner never relabels, and the Run instructions forbid the agent to.
  Taking published work out of the `ready-for-agent` queue is the Skill half,
  bradcstevens/git-loopy-skills#81.
- **Human-only acts with no structured tracker field**, such as the prerelease tag #592 waits on.
  Nothing machine-readable says "a human must tag". Those stay the agent's to discover, and a
  human's to move to `ready-for-human`.
- **Merging, updating or reviewing the pull request.** A human merges.
- **A pull request's checks, reviews or mergeability**, and any transitive readiness. One hop
  stands.
- **A pull-request candidate's own admission.**

## Vocabulary

`CONTEXT.md` gains **Awaiting merge**. It widens **Readiness**, **Pickup skip**, **Unresolved
readiness**, **Pin**, **Membership read**, **All-skipped Run**, **All-blocked Run** and **Routing
preparation** to take it in. **Blocked** keeps its meaning, and its `_Avoid_` line now says that a
closing pull request is not a dependency.

## Consequences

- **Nothing changes yet.** The Python, shell and PowerShell suites are green unchanged. The
  decision, the vocabulary, the contract amendment and the fixture land together, and these
  tickets implement them:
  - [#693] makes a Readiness verdict carry its own reason and unbound-Pool class;
  - [#695] makes the Python serial path read closing pull requests, and confirms the gh floor;
  - [#696] extends that to Parallel mode;
  - [#697] and [#698] port it to the shell and PowerShell Orchestrators;
  - [#694] names the pull requests to merge in the Unbound-Run notice.
- **Readiness costs a request per read.** A read carrying closing references pays
  `ceil(d / 100)` state requests. A read carrying none pays nothing.
- **A failed state read stops work it cannot prove.** A candidate whose closing pull request's
  state could not be read is `readiness_unprovable`. A Pool that bound nothing and holds one ends
  `preflight_failed`, naming it.
- **A Run whose only work waits on merges ends `all_blocked`.** Its Pickup skips, or a Rolling
  Run's `refusals`, name every pull request it waited on.
- **The Skill half is still owed.** Until bradcstevens/git-loopy-skills#81 lands, published work
  keeps `ready-for-agent`. This decision makes that cost a Pickup skip rather than a session.

[#693]: https://github.com/bradcstevens/git-loopy/issues/693
[#694]: https://github.com/bradcstevens/git-loopy/issues/694
[#695]: https://github.com/bradcstevens/git-loopy/issues/695
[#696]: https://github.com/bradcstevens/git-loopy/issues/696
[#697]: https://github.com/bradcstevens/git-loopy/issues/697
[#698]: https://github.com/bradcstevens/git-loopy/issues/698
