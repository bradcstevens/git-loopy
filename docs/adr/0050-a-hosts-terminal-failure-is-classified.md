# ADR-0050: A host's terminal failure is classified, and only a breach reaches the issue's ledger

**Status:** accepted

Decided by [#447](https://github.com/bradcstevens/git-loopy/issues/447), under the **Execution
host** spec [#445](https://github.com/bradcstevens/git-loopy/issues/445) §A.

§A settles that a host returns "a terminal failure" and that "the Run — not the host — decides
what happens next". It never states the decision. That gap stopped #447's remediation dead:
the seam computes a rejection and the caller discards it, so a branch the host refused is
accounted, Strike-ledgered and merged as if clean — and the fix cannot be written without
knowing what the Run is supposed to do instead.

This ADR states it. A **Lane contribution**'s terminal failure is one of three named classes,
and the disposition of each falls out of a single question: **did an agent session reach an
ending?**

## Three classes, two dispositions

`breach` — the host ran the contribution and what it returned violates the outcome contract.
`never_started` — dispatch or setup failed and no session ran; §L's class, owed by
[#462](https://github.com/bradcstevens/git-loopy/issues/462). `stall` — the host neither
delivered nor proved it failed; §E's *"proves nothing"*. The set is closed, and it is carried
as its own field rather than parsed out of the free-form detail.

Three names, but only two dispositions: `never_started` and `stall` are disposed of
identically. They are kept apart anyway, because they are different facts about the host and
an operator reading a trace must be able to tell *the host never picked it up* from *the host
went dark*. §K has already budgeted the `wrapper.contribution.end` `reason` widening that
publishing them costs, and names the never-started literal explicitly.

`stall` is **host-level silence only**. It does not reach the local placement at all, and in
particular it does not cover a local agent session that hangs: that path already resolves to
`SessionTermination.TIMED_OUT` and then to `SessionOutcome.TIMEOUT`, which is not a retryable
ending, so it moves the issue straight to `skipped` and charges its **Strike** on the first
occurrence. Folding it into `stall` would silently stop charging that Strike and leave a Run
of hanging sessions grinding forever.

## The ending is the whole decision

Since [ADR-0041](0041-the-strike-counts-issues-given-up-on.md) the **Strike** is charged in
exactly one place — the `skipped` transition, reached from a **Session outcome**. So Strike
accounting is not a second question to answer. Neither are demotion, escalation, or the
**Attempt lifecycle**. All four hang off one decision: is the ending offered?

A `breach` offers it, exactly as it does today. The agent ran to a real ending; the host then
refused what it produced. That ending is evidence about the *issue*, and both ledgers are per
issue rather than per mode. §J's blameless shelter does not reach it either — a stopped
contribution is blameless because "the existing demotion rule already refuses to count work
that was still running", and a breach's work was not still running. The same instinct governs
the vocabulary itself: `TIMEOUT` and `CRASH` are facts about the session alone, and a commit
that landed first does not launder them. A host's refusal launders them no better.

`never_started` and `stall` have no ending to offer, and that is the entire mechanism. The
Attempt ledger already spends nothing on an absence — an absence is not a failure to advance —
so §L's *"never a Strike"* and #462's *"no Strike and no demotion"* cost **no code**. They are
consequences of not manufacturing an ending, not a blamelessness rule anyone has to maintain.

The injustice this leaves is real and is accepted: a host that breaches its own contract costs
the **issue** an Attempt and the **Run** a Strike. The alternative is worse. Shielding a breach
would mean an agent session that ran, burned a pair and produced nothing usable is free, and a
Run whose host is subtly broken would grind through its whole **Pool** at full price with the
ceiling untouched.

## The ending travels on the outcome

Both outcome variants carry the session's ending, optional on each. `ContributionSuccess`
always has one; a `breach` has one; `never_started` and `stall` carry none. **The optionality
is the class distinction**, which is why the blameless half needs no branch.

This kills the side-channel #447 shipped, where the local runner stashed the ending in a dict
keyed on `id(request)` and the caller popped it back out. That dict was populated only by the
local runner, so any substituted host returned a well-formed outcome and then raised
`KeyError` in the caller — the seam was injectable in shape and not in fact.

Putting the ending in the outcome does not breach §A's six refusals. Reporting a fact is not
deciding one, and §A already has a host return that contribution's **Events**, which is a far
larger payload than its ending. Deriving the ending from those Events instead would be more
placement-neutral, and is the right end state once a remote host ships Events as an
end-of-job artifact; no derivation path exists today and building one is not this seam's job.

## A failure is terminal, and the Run adds no retry

§A forbids a **host** from silently retrying. The Run does not retry either.

A failure is terminal for that contribution. The issue, having spent no Attempt, stays eligible
and the ordinary **Pool** re-offers it on a later scheduler turn — which is the re-attempt, and
it costs no new mechanism. Repeated host failure narrows the Lane count through the existing
host-and-setup pressure input, exactly as §L requires, and then ends the Run as an
**environment failure** rather than spinning: #462 already owes that disposition for a failing
green-base preflight, and a Run that cannot dispatch is the same fact arriving later. §L
forbids inventing a new signal, and a per-issue dispatch counter that is neither an Attempt nor
a Strike would be one.

The threshold — what counts as *repeated* — belongs to #462, which holds both the disposition
and the pressure routing, and which is where a remote host first makes these two classes
reachable. #447 cannot test a threshold for failures the local host cannot produce.

## Integration is forbidden, and nothing enforces it

No **Integration** follows a terminal failure. This needs no guard: a failure resolves to the
scheduler's terminal disposition, which finalizes the contribution and returns before
Integration is reached. §E forbids it independently — Integration merges a completion SHA, and
a failure carries none.

A `breach` whose tree was dirty may have real agent commits on its Lane branch, and they are
discarded from base. That is already today's behaviour, and §F keeps the branch alive
regardless: a Lane branch is collected when merged into base **or** when its issue closes. The
work is recoverable, not useful — the same property [#452](https://github.com/bradcstevens/git-loopy/issues/452)
states for salvage.

## A completion SHA is constitutive of a success

A host that cannot name the SHA its branch resolves to has not succeeded. §E requires the
triple and says Integration "merges the SHA, never the name", with the fetch SHA-pinned; a
success with no SHA is not a success that section recognises. So the unresolved-SHA failure is
**entailed**, not invented — it reads as a `breach` like any other.

The alternative — an optional SHA on `ContributionSuccess` — weakens the success shape for
every host to accommodate one degenerate local case, and moves the check downstream to
Integration from the boundary where it belongs.

This is the one place the seam's verdict differs from the behaviour #447 replaced, where a
failure to read `HEAD` was not a contribution failure at all. #447 promised byte-identical
Events, and this is a knowing exception to it: integrating a branch whose head cannot be read
is not behaviour worth preserving.

## Retention is unchanged

The disposition owes worktree retention nothing. Today's rule keys on the per-Lane
**Checkpoint** outcome; §F's keys on whether **salvage** succeeded. Because a Checkpoint *is*
a salvage, the two agree on every reachable case — a failed Checkpoint is a failed salvage and
preserves, and every path that leaves a clean tree reclaims. #452 retires §3.10's forensic
wording later without changing a single outcome.

## Considered and rejected

- **One undifferentiated terminal failure.** Simplest, and it ships no dead branches. Rejected
  because §E and §L already give three different answers about the Strike at two neighbouring
  moments; collapsing them here would make the same failure blameless at dispatch and blameful
  at the seam.

- **Reuse `checkpoint_ok` or `changed` to force the terminal disposition.** Free — both already
  route to a terminal reason, and for a dirty-tree breach `checkpoint_ok=False` is literally
  true, since the two coincide on every path. Rejected because they are the *local runner's*
  answers to *local* questions: a remote host runs no local Checkpoint, so overloading them
  re-imports the local placement into a placement-neutral seam. It stays true only while
  `local` is the only host.

- **Bounded retry inside one contribution.** Keeps the wire quiet and the Summary honest at one
  row per issue. Rejected because the retries then cost real dispatches that no reader can see,
  and the bound becomes a knob at the moment §G finished deleting them.

- **Shield a breach from the Strike specifically.** Answers the injustice above directly.
  Rejected under "The ending is the whole decision".

- **Let `stall` cover a local session timeout.** Symmetric, and it reads naturally. Rejected
  because it silently retires the Strike that a hung agent charges today.

## Consequences

- **The seam becomes substitutable in fact.** Deleting the `id(request)` side-channel is what
  lets a fake host run through the parallel loop, which is #447's second acceptance criterion
  and is unmet today.

- **Three new `reason` literals** on `wrapper.contribution.end`, riding the single
  `event_schema_version` step §K has already earned for the `reason` widening.

- **Two of the three classes are unreachable until a remote host exists**, so they ship exercised
  only by a fake. That is the intended shape — the seam's whole purpose is that
  [#451](https://github.com/bradcstevens/git-loopy/issues/451),
  [#460](https://github.com/bradcstevens/git-loopy/issues/460) and #462 implement one decision
  rather than three compatible guesses.

- **§A's Checkpoint-trailer rule is not settled here** and gets its own ticket. It is
  unenforceable at the seam as stated: git-loopy cannot tell a host-authored commit from an
  agent-authored one, and the cheap check — reject any commit carrying a closing keyword —
  fails because the prompt *requires* the agent to close its own issue. That ticket decides
  whether an authorship discriminator is invented or the rule is recorded as an obligation with
  no runtime check.

- **The Execution host ADR owed by §M** ([#465](https://github.com/bradcstevens/git-loopy/issues/465))
  supersedes ADR-0001, ADR-0024 and ADR-0010 and describes the seam itself. It does not
  supersede this one, which is about what the Run does with the seam's failing half, and should
  cross-reference it.

- **No glossary entry lands yet.** §M sequences glossary entries with the code that implements
  them.
