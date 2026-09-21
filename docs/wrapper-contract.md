# The Wrapper Contract

> The language-neutral behavioural specification that **every** git-loopy **Orchestrator** — the
> Python reference runner and the shell, PowerShell, and future Rust ports — must satisfy. This
> is the single source of truth the [**Runner family**](../CONTEXT.md#the-runner-family)
> implements and the [**Conformance suite**](../git-loopy/conformance/README.md) pins. See
> [ADR-0013](adr/0013-multi-language-runner-family.md) for why the family exists and how it stays
> in lockstep.

**Contract version:** 2.9 (tracks the Python reference implementation in `git-loopy/python/`).

Terminology in **bold** (Run, Iteration, Pool, Strike, Checkpoint, Active issue, ...) is defined
in [`CONTEXT.md`](../CONTEXT.md). Where this spec and the Python code disagree, the code is the
temporary tie-breaker and the discrepancy is a contract bug to be reconciled — the intent is that
they never disagree, enforced by the Conformance suite.

Requirement levels use RFC-2119 **MUST / SHOULD / MAY**. Each invariant is tagged with the roadmap
phase that first requires it, so the phase-1 Conformance suite can pin the core loop before the
TUI, config, OTel, and parallel-mode phases land.

---

## 1. Preflight (phase 1, MUST)

Every Orchestrator MUST expose top-level `git-loopy --version` as an earlier public identity seam.
`--version` accepts no additional arguments, reads the distribution's shared **Release version** as
strict UTF-8 Semantic Versioning, writes exactly `git-loopy <Release version>` plus one newline to
stdout, writes nothing to stderr, and exits `0`. It MUST complete before configuration parsing,
repository discovery, dependency checks, Run preflight, Event initialization, or artifact creation.
Unavailable or invalid Release metadata MUST fail nonzero with no stdout and an explicit stderr
diagnostic; an Orchestrator MUST NOT substitute an `unknown` or compatibility version.

Before the first **Iteration**, an Orchestrator MUST verify its preconditions and, on failure,
exit `1` **before** doing any work:

- `docs/agents/issue-tracker.md` exists (the signal that `/setup-git-loopy-skills` has run). If
  absent, exit `1` with a stderr message pointing the operator at `/setup-git-loopy-skills`. The loop
  MUST NOT invoke `/setup-git-loopy-skills` itself (it is interactive and unsafe under
  `copilot --yolo -p`).
- `gh` is authenticated, and `git`, `copilot` are on `PATH`. The shell port additionally requires
  `jq`.
- The resolved `PROMPT.md` exists (see §4).

## 2. Collection (phase 1, MUST)

At the start of every **Iteration**, an Orchestrator MUST rebuild the **Pool** from scratch —
never cache across iterations:

- List every **open** issue labeled `ready-for-agent` via `gh issue list`.
- (PR mode, phase 3+ / opt-in) When PR support is enabled, also list open `ready-for-agent`
  pull requests.

An empty Pool at the start of an Iteration is the **clean-exit-on-empty** condition (exit `0`,
§10) — but only when the read that found it empty was *complete* (§2.2). The next Iteration's
collection — not any sentinel — is the source of truth on whether work remains.

### 2.1 Fetch completeness (contract 1.11, MUST)

The candidate list MUST be read **to completion**, and the read MUST request `created_at` among
its fields.

`gh issue list` pages internally up to whatever `--limit` asks for, so a page *shorter* than the
requested limit proves the source had nothing more to give and a page exactly at the limit proves
nothing. An Orchestrator MUST therefore re-ask with a **doubled** limit until a page comes back
short or the ceiling is reached. A fixed limit was survivable while the Pool was ordered
newest-first: it hid the *oldest* candidates, which nothing was about to select. Under §3.2 it
hides the *newest* — so an issue filed today, including a **Priority** one, would fall outside the
window exactly when it matters most.

#### The read schedule (contract 1.15, MUST)

The schedule is **shared**, not each member's own: the first ask MUST be `100` and the ceiling
MUST be `1600`, so the walk asks `100, 200, 400, 800, 1600` and stops. `issue-ordering.json`'s
`read_schedule` declares both, and its `read_cases` pin the walk each backlog produces.

This is a §3.2 obligation wearing a §2 hat. Two members that agree on every ordering rule and
disagree on the ceiling read *different backlogs* from the same repository, and a backlog is
exactly what §3.2 orders — so the divergent answer arrives as a different **Active issue** without
either member sorting anything differently, and nothing in the Event stream says why. The three
numbers were private constants agreeing by convention alone, which is the drift the shared
`created_at` field set was named once to prevent.

The decision one page forces — `complete` when it came back short, `continue` at a doubled limit
when it was full below the ceiling, `incomplete` when it was full *at* the ceiling — MUST be a
seam the Conformance adapter can call directly, for the same reason §3.2's comparison is one: an
adapter that reproduced the walk would agree with itself while the Orchestrator read a different
backlog.

A read still full at its ceiling is **not authoritative**: it establishes neither that the Pool is
empty nor which issue is the head of the order. An Orchestrator MUST report such a read to the
operator and MUST NOT treat it as the whole backlog. It is not a Run failure — selection is
unattended, and a partial Pool is still work.

`created_at` MUST ride the collected candidate rather than being fetched later: §3.2 orders
*collected items*, so a candidate that only acquires its timestamp at an authoritative per-issue
read acquires it after the decision that needed it. A member whose JSON reader coerces
ISO-8601-shaped strings into a native date type MUST read this field through a reader that does
not — a re-rendered value is a different value, and a coercing reader is typically *wider* than
§3.2's grammar, so it would silently rescue timestamps the rest of the family calls `malformed`.

A source with no page limit — the local-markdown Pool's directory walk — is complete by
construction and MUST report itself so, including when it is empty.

### 2.2 An unread Pool is unknown, not empty (contract 2.6, MUST)

Emptiness is a claim only a **complete** read may make. A read that failed outright, that gave out
part-way through, or that is still full at §2.1's ceiling produces the same zero candidates a
finished backlog does — the two are byte-identical in the data — so an Orchestrator MUST decide the
clean-exit-on-empty condition from *completeness together with* the candidate count, never from the
count alone.

An Orchestrator that concludes a Run on such a read MUST NOT report the exit-`0` empty Pool: exit
`0` says "there is no work", and an unattended caller acts on that by going home. It MUST instead
end under `preflight_failed` and its non-zero exit, which is the reason §1 already spends on "a
precondition this Run needs is not satisfied, and an operator can repair it" — an unreadable
tracker is exactly that, and §3.3.1's `gh`-capability gate is the *predictable* half of the same
failure, caught earlier only because it is visible in `gh --version`. A host-side capability the
gate cannot see (a server without issue dependencies, an expired token, a 502) fails later for the
same reason and takes the same operator action, so it takes the same reason rather than splitting
one repair across two vocabularies.

The rule MUST be one rule, asked wherever a Pool read could end a Run — in the Python reference it
is `sources.confirms_empty_pool`, which both the serial Iteration and Rolling dispatch's terminal
classifier call. Two dispatch paths that each restate it drift, and the drift is invisible: both
report `empty_pool` and only one of them is entitled to.

An Orchestrator that holds *independent* evidence about the same Pool is not overruled by one
refused read. Rolling dispatch's granted serial turn already latched proof that specific
serial-required issues exist, and its serial fallback already has two complete same-turn reads
proving the Pool empty; in both, a later read that proved nothing MUST NOT overturn what was
proven, and the Run polls for a read that completes rather than abandoning work it can name.

## 3. Discriminator (phase 1, MUST)

The Pool MUST be filtered to issues whose body contains **both** literal section headings:

- `## What to build`
- `## Acceptance criteria`

A `## Parent` section is optional. Issues missing either required heading (bare PRDs) MUST be
skipped. GitHub issues whose titles begin with `PRD:` or `Spec:` (case-insensitive)
MUST also be excluded, even with both headings, `ready-for-agent`, `priority`,
`parallel-safe`, or an explicit `--issue` pin. They are planning documents, not
executable tickets. Check the title on both list and authoritative reads.
In PR mode a PR is kept only if it carries an `## Agent Brief` (in its body or any
comment) — the PR analogue of the discriminator.

### 3.1 Pool exclusions (contract 1.5, MUST)

A skipped candidate MUST be reported, not dropped silently. `ready-for-agent` is a *human*
assertion — somebody deliberately triaged that issue — so an Orchestrator that declines it owes
the operator the reason. An Orchestrator MUST, for every `ready-for-agent` candidate the
discriminator rejects, report the candidate's reference and one **exclusion reason** from this
closed vocabulary:

| Reason | Meaning |
| --- | --- |
| `missing_what_to_build` | `## Acceptance criteria` present, `## What to build` absent |
| `missing_acceptance_criteria` | `## What to build` present, `## Acceptance criteria` absent |
| `missing_both_sections` | Neither required heading present |
| `planning_document` | Issue title begins with `PRD:` or `Spec:`; takes precedence over missing headings |

The reason MUST be derived from the same discriminator pass that decides membership, so the
reported reason and the membership decision cannot disagree. `discriminator.json` pins the
vocabulary and the reason for every case.

A candidate the source could **not read** (a failed per-issue view, an unreadable file) is NOT an
exclusion — it was never discriminated against, and reporting it as one would send the operator to
fix headings that are probably fine. The existing warn-and-skip path continues to cover it.

A candidate that is not **ready** — one carrying an open native `blocked_by` dependency, or one
whose dependencies could not be read — is likewise NOT an exclusion (contract 2.0). The exclusion
vocabulary above is **closed at those four reasons**, and readiness MUST NOT be added to it. An
exclusion is an authoring mistake a human must fix; a blocked candidate is correctly authored work
whose turn has not come, and it clears itself when its last blocker closes. It MUST remain in the
**Pool** — the closure whitelist, the collection Event and the emptiness test all still need to
see it, and a Pool that is *empty* ends the Run cleanly (§10) where a Pool that is merely *waiting*
has not run out of work. Readiness is decided at **Pickup** and at **Lane candidacy** instead
(§3.3, §3.3.1), never here. See
[ADR-0047](adr/0047-a-blocked-issue-is-not-pickup-admissible.md).

Exclusions MUST be reported as `wrapper.pool.excluded` Events (§12), before the
`wrapper.afk_ready.collected` they explain, and MUST also reach the operator's own output rather
than only a debug log. An **empty eligible Pool caused entirely by exclusions** MUST read
distinctly from a Pool that simply held no `ready-for-agent` work: the two demand opposite
operator responses. It does **not** change the exit code — both remain the clean-exit-on-empty
condition (§10).

Reporting an exclusion MUST NOT change Pool membership, and MUST NOT cost an extra source
round-trip: the cheap list read already carries the body the decision is made on.

### 3.2 Selection order (contract 1.10, MUST)

The eligible Pool has **one total order**, and it is part of this contract rather than each
member's own sort. All three Orchestrators previously called `gh issue list` with no sort
qualifier and inherited GitHub's unstated `sort=created&direction=desc`; the resulting
newest-first order was a CLI default nobody decided, and an issue could be passed over
indefinitely with no mechanism to notice (ADR-0032). Two members agreeing on eligibility while
disagreeing on *which* eligible issue comes first would pick different work from identical
input, and nothing in the Event stream would say why.

An Orchestrator MUST order eligible issues ascending by, in order of significance:

1. **Priority rank** — an issue carrying the `priority` label ranks ahead of one that does not.
   **Priority** is a *human assertion*, read like `parallel-safe` and never inferred from issue
   content, and it reorders work **without changing eligibility**: a Priority issue still needs
   `ready-for-agent`, still MUST pass the §3 discriminator, and still needs `parallel-safe` to
   enter a **Lane**.
2. **Creation timestamp** — the issue's `created_at` as the source reports it, ascending, so the
   oldest eligible issue is the head of the order. It MUST be read from the source and MUST NOT
   be computed locally or mutated.
3. **Issue number** — ascending. This is what makes the order **total**: no two distinct issues
   may compare equal, so the head of the order is one issue rather than a set.

The comparison MUST be a pure function of the fetched issue fields, exposed as a production
decision seam the Conformance adapter calls directly, and MUST NOT depend on the order the
source listed the issues in. Ordering the same input twice MUST yield the same sequence.

A **Task type** MUST NOT affect the order. It selects a **Routed pair** (§14) and nothing else.

A usable `created_at` is `YYYY-MM-DDThh:mm:ss`, optionally followed by a fractional second of
one to nine digits, followed by `Z`/`z` or a `±hh:mm` offset, naming a calendar-valid date in
the years **1970-9999**. Offsets MUST be normalized to an instant before comparison — comparing
the text would order `2026-03-01T01:00:00+01:00` after `2026-03-01T00:45:00Z`, when it is
fifteen minutes earlier. The grammar is deliberately narrow: every widening is a value the
members can disagree about, and an order they disagree on is worse than a value one of them
refuses by name.

An issue whose `created_at` is absent or does not satisfy that grammar MUST sort **last within
its own priority rank** — not last overall, because a broken field is not a retracted human
assertion — and MUST be reported with one **timestamp defect** from this closed vocabulary:

| Defect | Meaning |
| --- | --- |
| `absent` | The source carried no value (a null or empty field) |
| `malformed` | A value arrived that is not a timestamp in the accepted grammar |

The Run MUST NOT fail over an undated issue. Selection is unattended — `ready-for-agent` means
*ready for autonomous execution* — so an unusable ordering field is warned about and worked
around, never blocked on.

`issue-ordering.json` pins the order, the head it selects, and the defect every undated issue
carries. Eligibility is deliberately **absent** from that fixture: it is `discriminator.json`'s
decision and the `ready-for-agent` label's, and restating it beside the order would give one
rule two homes that could disagree.

#### The Pin (contract 1.14, MUST)

An Orchestrator MUST accept `--issue N`, which **pins** issue `N` for one invocation: the
Orchestrator works `N` instead of the head of the order above. Order is a policy, and an operator
sometimes has to override it for a single Run without weakening it for everyone.

The pin **bypasses the order and nothing else** (ADR-0032), which is four separate obligations:

1. **It is applied to the finished order, not folded into the comparison.** The pinned issue is
   moved to the head and every other issue keeps its §3.2 sequence behind it. The sort key stays
   what §3.2 requires — a pure function of the fetched issue fields — because a pin is one
   operator's instruction for one invocation and is not a property of any issue. A Run therefore
   resumes oldest-first the moment its pinned issue leaves the **Pool**.
2. **It outranks Priority.** A pinned issue reached the head because an operator named it,
   whatever its labels said. Were **Priority** to win, `--issue N` would work on most
   repositories and silently do nothing on exactly the ones that use the label. `wrapper.pickup.
   bound` MUST report `reason: pin` for that binding, and `order`/`priority` for every other
   binding in the same Run — the pin explains one binding, not the whole Run.
3. **It does not bypass eligibility, and an ineligible pin FAILS the invocation.** A pinned issue
   that is closed, missing, unreadable, lacks `ready-for-agent`, or fails the §3.1 AFK-ready
   discriminator MUST end the invocation with the `preflight_failed` exit code, naming what is
   wrong — and for a discriminator failure, naming the **planning document** or **specific missing section**, so the
   operator can fix the issue rather than guess. It MUST NOT fall back to the head of the order:
   §3.3 makes a candidate the runner cannot take a *skip* precisely because a serial Run merely
   walked past it, whereas a pin is an operator naming an issue, and there is no next candidate
   that honours what they asked for. Silently working a different issue than the one named is
   worse than stopping. In **Parallel mode** the pin MUST additionally carry `parallel-safe`,
   because a **Lane** Pool requires it and a pinned issue that never enters the Pool would leave
   the Run working the head of the order — the same silent substitution, arrived at by omission.
4. **It weakens nothing for any other issue.** The pin promotes; it does not restrict the Pool.
   Every other candidate remains eligible on exactly the terms §3.1 and §3.2 already set.

Exactly **one** issue may be pinned per invocation; a second `--issue` MUST be rejected as a
**usage error** (§10's `usage_error`, not `preflight_failed`) rather than resolved by taking
either value. The invocation is malformed, and "the last one wins" is the silent substitution
this section exists to prevent, arrived at by a CLI parsing default.

The pin MUST be invocation-scoped, and therefore MUST NOT be expressible as a label, an
environment variable, or a persisted Config key. All three outlive the invocation: a label is
global to the tracker and would point every concurrent Run at the same issue, an environment
variable is inherited by every Run launched from that shell, and a Config key by every Run in
that checkout. A flag is the only surface whose lifetime matches the thing being expressed.

Validation MUST happen **once, at preflight**, before the Pool is read. Once per Iteration would
end a healthy Run the moment it legitimately closed the issue it was pinned to, and validating
after the Pool would make an ineligible pin indistinguishable from a backlog that simply did not
contain it. `issue-ordering.json` carries the pin as a per-case `pin` field and pins that it
outranks Priority; the *refusal* is not in that fixture, for the same reason eligibility is not.

### 3.3 Serial Pickup (contract 1.12, MUST)

A serial **Iteration** MUST bind its **Active issue** *before* the agent session starts, from the
head of the §3.2 order. Selection is the runner's decision; the agent's is task type and nothing
else.

Until contract 1.12 the serial loop handed the agent the whole Pool with an instruction to rank
it, and learned which issue had been chosen from a **Working marker** the agent wrote mid-session
(§12, `binding_source: working_marker`), with `closure`, `commit`, and `single_member_pool` as
after-the-fact fallbacks when no marker arrived. That made §3.2 unobservable in serial mode — the
runner ordered a Pool whose order nothing consumed — and made the binding retroactive: everything
the agent produced before the marker had to be re-attributed once it landed.

An Orchestrator running serially MUST:

- **Pick before the session.** Walk the ordered Pool front to back and bind the first candidate it
  admits, emitting `wrapper.issue.activated` with `binding_source: serial_pickup` and the Pickup's
  own UTC instant. The binding is **prospective**: it precedes the first `agent.output` of the
  Iteration, so no output is ever attributed retroactively.
- **Render exactly the bound issue.** The prompt's issue set MUST carry that one issue (§4), not
  the Pool. An Orchestrator MUST NOT instruct the agent to select from a set.
- **Skip and advance, never fail.** A candidate the Orchestrator cannot admit — one whose
  `task-type:` label refuses to resolve a **Routed pair** (§14), one the **Attempt lifecycle** has
  already defeated this Run, or one that is not **ready** (§3.3.1) — MUST be passed over and the
  next candidate tried. A serial Run has no second Lane to leave the candidate for, so a refusal
  that ended the Run would end it over one mislabelled issue. Each skip MUST be reported with the
  issue and the reason, as a `wrapper.pickup.skipped` Event (§12) and not only as a diagnostic.

  **The admissible set is open, and it governs every Pickup (contract 2.0).** Admission is
  whatever an Orchestrator must resolve *at Pickup* in order to start the session on the bound
  issue. The contract names three members: §14's **Routed pair** — which §14 records as *"future
  phase-3 work"* for the native ports — the **Attempt lifecycle**, which refuses a candidate this
  Run has already given up on, and **Readiness** (§3.3.1). A member that resolves none of them
  admits every candidate and binds the head of the order, which satisfies this rule rather than
  skipping it: it has nothing it can refuse. When a native port implements one it acquires that
  refusal and this bullet with it, and its skips and its all-skipped Strike MUST then match the
  reference member's.

  The set is **open**, and this is what may join it: a refusal is admissible when it is a fact the
  runner must resolve at Pickup to start the session, and when it is **not a second opinion on
  something a human already asserted**. Eligibility — the `ready-for-agent` label and the AFK-ready
  discriminator (§3.1) — is exactly such an assertion, and re-deciding it here would be a second
  place for it to disagree. Readiness is not: it is a fact about the tracker's dependency graph,
  not about how the issue was authored.

  The admissible set is a property of **Pickup**, not of the serial loop: it governs a **Lane**
  pickup (`binding_source: lane_pickup`) exactly as it governs this one.
- **Record the binding (contract 1.13).** A **Pickup** that binds MUST emit
  `wrapper.pickup.bound` (§12) carrying the issue, the selection reason — `pin` when §3.2's
  **Pin** named this candidate, else `priority` or `order` — and where the candidate sat in the
  order. Selection is the runner's decision as of contract 1.12, and a decision nobody
  can see is a decision nobody can audit: the starvation §3.2 exists to end was invisible
  precisely because being passed over left no trace. The record is emitted *after* the
  `wrapper.issue.activated` that publishes the binding, so it never describes a binding the rest
  of the stream does not contain, and every skip that ended in this binding MUST precede it.
  An Orchestrator that resolved a **Routing resolution** at this Pickup MUST carry it on the same
  record (contract 1.21, §14) rather than on an Event of its own.
- **Select and publish are two steps.** The prompt renders the candidate the Pickup *selected*,
  even when publishing its activation fails. An Orchestrator MUST NOT fall back to rendering the
  whole Pool on a failed activation: that restores the menu this section removes, on the one path
  where the runner has already privately chosen a head. The Iteration proceeds unbound — §12's
  fallbacks are exactly what that case is for — and MUST say so.
- **Distinguish "all skipped" from "empty".** A Pool that is empty ends the Run cleanly (§10). A
  non-empty Pool in which *every* candidate was skipped is an Iteration that did no work: it MUST
  count a **Strike** and the Run MUST continue, because the condition is a labelling mistake an
  operator can fix while the Run is still alive.

A **Working marker** remains part of `PROMPT.md`, but its meaning changes with the binding: it is
**attribution, not selection**. A marker naming the bound issue is confirmation. A marker naming a
different issue MUST NOT rebind — the Pickup stands, the disagreement is warned about, and the
marker is recorded rather than obeyed. This is the same immutability §12 already requires of the
first activation; contract 1.12 only moves which event is first.

Serial Pickup does not change **Rolling dispatch**: a **Lane** already binds one issue at pickup
(`binding_source: lane_pickup`) and keeps doing so — and emits the same `wrapper.pickup.bound`
record when it does, because an operator auditing selection is asking one question about a Run,
not two questions about two schedulers. It applies the same admissible set, so a **Lane** MUST
refuse candidacy to a candidate that is not **ready** rather than reserving it and releasing it
again each turn.

### 3.3.1 Readiness (contract 2.0, MUST)

A candidate carrying an open native `blocked_by` dependency is **not admissible**. An Orchestrator
MUST pass it over and try the next candidate in the §3.2 order. The candidate stays in the **Pool**
(§3.1), and being passed over MUST NOT count a **Strike** — it was never attempted, so it costs
nothing and is reconsidered on the next **Iteration** with no human touching the issue. See
[ADR-0047](adr/0047-a-blocked-issue-is-not-pickup-admissible.md).

**The read.** An Orchestrator MUST resolve readiness through the **GraphQL** `blockedBy` connection,
not through REST. REST is documented to undercount cross-repository dependencies and to do so
silently — there is no `totalCount` to notice the shortfall by — so a REST read can report a blocked
candidate as ready, which is the one outcome this section exists to prevent. `gh issue list` and
`gh issue view` both serve `--json blockedBy` from GraphQL (`gh` 2.94.0 and later), so the
requirement is on the *source of the connection*, not on which `gh` subcommand fetched it.

**The connection rides a read already being made.** An Orchestrator MUST NOT pay a per-candidate
round-trip for readiness. `blockedBy` MUST be requested on the §3.1 collection read and the
**Membership read** (§9), then carried with each candidate. Thus collecting a **Pool** and
refreshing Membership cost nothing extra however large the Pool grows.

**Pickup** (serial, §3.3) takes the readiness verdict while it walks the ordered Pool, from the
connection the collection read carried for that candidate. It MUST NOT issue a second dependency
read to decide that verdict. **Lane candidacy** (Parallel mode, §9) takes its verdict from the
continuously refreshed **Membership read**, which is the only read a scheduler turn takes. A Lane
that reserves a candidate still performs its normal Pickup validation; candidacy is a *cheaper
refusal taken earlier*, never a replacement for that validation.

A **Membership read** that could not determine a candidate's blockers leaves readiness **unknown**,
which is not ready — matching how an incomplete read already leaves the Pool's emptiness unknown
(§9) rather than reporting it empty.

**One hop.** An Orchestrator MUST read the candidate's own `blockedBy` connection and MUST NOT
traverse the dependency graph further. Transitive traversal is a **non-goal**: it computes a
property of the graph rather than reading an assertion about an issue, and it is unbounded in
depth. A candidate blocked by *itself* is therefore **blocked** like any other; a cycle of length
two or more is indistinguishable from an ordinary blocker at one hop, and no member is required to
detect one.

**A blocker in another repository blocks**, identically to one in this repository, and the reported
reason MUST carry the full `owner/repo#number`. The verdict MUST NOT depend on where the blocker
lives: a `blocked_by` edge is a human assertion, and it does not weaken by crossing a repository
boundary.

**Completeness.** `blockedBy` is a paginated connection. An Orchestrator MUST request at least
GitHub's per-issue cap of 50 links in a single page; asking for fewer is a member defect, not an
expected state. Where the returned nodes do not account for `totalCount`, or a node comes back
unreadable, readiness has **not been proven** and the candidate MUST be skipped — under
`readiness_unprovable`, never under `blocked_by_open_dependency`. The two are different facts: the
first reports that no assertion could be read, and there may be no blocker at all; the second
reports an open blocker that was read. Reporting an unprovable read as blocked would assert the
very thing the read failed to establish, and would tell an operator to wait for a blocker to close
when the wait can never end.

**Reason vocabulary.** A readiness skip MUST report one reason from this closed vocabulary on its
`wrapper.pickup.skipped` Event (§12):

| Reason | Meaning |
| --- | --- |
| `blocked_by_open_dependency` | At least one `blocked_by` dependency was read and is open |
| `readiness_unprovable` | The `blockedBy` connection was incomplete or a node was unreadable |

`issue-readiness.json` pins the verdict and the reason for every case.

**An unread refusal may not establish a terminal Pool fact (contract 2.7, MUST).** A Pool that bound
nothing ends the Run under one of three reasons, and `readiness_unprovable` outranks the other two.
Where **every** candidate was refused and **at least one** refusal was `readiness_unprovable`, the
Run MUST end under `preflight_failed` (§10) — not `all_skipped`, and not `all_blocked`. Where every
refusal was `blocked_by_open_dependency` the Run ends `all_blocked`, and otherwise `all_skipped`,
both exactly as before. `conformance/exit-codes.json` pins this as `unbound_pool_cases`, so the
family asks one rule rather than restating it per member.

This is §2.2's rule at the next seam down. `all_skipped` means "a labelling mistake an operator can
fix" and `all_blocked` means "every candidate proves an open blocker" — both are claims about the
**work**, and `readiness_unprovable` is a report about the **read**. A Pool nobody managed to read
may be entirely ready, so either verdict would assert the thing the read failed to establish.
`preflight_failed` states what is true instead: a precondition this Run needs is not satisfied and
an operator can repair it. Because an unprovable verdict carries no blockers by design, the ending
MUST name the candidates whose readiness could not be read, or an operator is handed exit 1 and
nothing to act on.

**Rolling dispatch is not bound by this the same way.** A Rolling Run holding independent evidence
of remaining work MUST keep quarantining and retrying an unreadable candidate rather than ending
(§12, *Rolling-dispatch contribution lifecycle*); the rule above governs the point at which a walk
that read the whole Pool ends the Run on its own evidence.

**Lane candidacy (Parallel mode).** A **Lane** MUST refuse **candidacy** to a candidate that is not
ready, rather than reserving it and declining it at its own Pickup. The **Attempt lifecycle** (§9)
fixed the shape: a Lane's only way to decline a reservation hands the candidate back to the list it
came from, so a candidate that will be refused every turn would be reserved, skipped and released
once per scheduler turn for the rest of the Run. Refusing candidacy says the same thing once.

Refusal is **not eviction**. The candidate MUST stay in the scheduler's cache, because the next
**Membership read** is the whole of what promotes it: a blocker closing mid-Run makes it
candidate-eligible on the following refresh, with no Run restarted and no human touching the issue.
This is what separates readiness from an **Attempt-lifecycle** defeat, which nothing inside the Run
can undo and which therefore does evict.

Readiness **composes** with the other candidacy predicates and MUST NOT replace any of them: a
candidate must still carry `parallel-safe`, must still pass the Attempt-lifecycle skip, and the
scheduler's own collision guard is untouched.

Because both seams read the same assertion, **both orders MUST agree**: a Lane MUST NOT reserve an
issue a serial Iteration of the same Run already found blocked, and a serial fallback taken while
Lane concurrency is throttled MUST NOT bind one the scheduler already refused. A candidacy refusal
is silent by design — it is the churn this rule exists to remove — while a serial Pickup skip
reports itself as §3.3.1 requires.

## 4. Prompt assembly & agent invocation (phase 1, MUST)

Each Iteration MUST feed a single `copilot --yolo -p` invocation with, at minimum:

- the issue set — for a serial Iteration, the **one** issue bound by §3.3; for a Lane, its own
  reserved issue,
- the last **five** commits, and
- the resolved **`PROMPT.md`**.

`PROMPT.md` resolution follows project → global → packaged precedence (the project copy wins).
Within the **project** scope the Orchestrator MUST probe the lowercase `git-loopy/prompt.md` first
and then the uppercase `git-loopy/PROMPT.md` (first hit wins): the kit ships the uppercase variant,
and probing the lowercase name first keeps the override resolvable on case-sensitive filesystems
(typical on Linux) while case-insensitive ones (APFS/HFS+ on macOS, NTFS on Windows) accept either
casing. The Orchestrator MUST capture the agent process's real exit status
(not the exit status of a pipe it is teed through) so an agent crash is never mistaken for a clean
turn. Streaming/live output is rendered per port (plain text in phase 1; the **TUI helper** from
phase 2).

## 5. Auto-close backstop (phase 1, MUST)

After the agent turn, the Orchestrator MUST walk the Iteration's **new** commit messages for
GitHub closing keywords and close any still-open referenced issue **that was in this Iteration's
Pool**, with a comment pointing at the commit SHA(s).

The close-keyword match MUST be equivalent to the reference regex
(`git_loopy.wrapper.CLOSE_KEYWORD_RE`):

```
(?i)(close[sd]?|fix(?:es|ed)?|resolve[sd]?)\s+#(\d+)
```

- Case-insensitive.
- Matched **line by line**, splitting on `\n` only (not on `\r`, `\v`, `\f`, or Unicode line
  separators — POSIX `grep` semantics).
- Referenced issue numbers deduplicated in **first-encounter order**.
- **Pool-whitelisted:** a `Closes #N` for an `N` not in this Iteration's Pool MUST be ignored, so
  a stale or mis-numbered reference cannot act on an unrelated issue. The whitelist is the
  **Pool**, not the §3.3 bound issue: an agent that finishes a neighbouring issue on the way
  through should still close it, and narrowing the whitelist to the binding would silently drop
  those closures.
- **Issues only:** the backstop MUST NOT close a PR. PRs are *advanced*, never closed, by the
  Orchestrator.

Any change to the commit-message convention in `PROMPT.md` MUST be mirrored here and in the
Conformance regex fixtures.

## 6. Progress & Strike accounting (phase 1, MUST)

An Iteration "made progress" **iff** it produced at least one **agent** commit **or** at least one
wrapper closure. (PR mode: a PR head-SHA advance also counts as progress.)

- A runner-authored **Checkpoint** (§7) MUST NOT count as progress.
- The legacy `<promise>NO MORE TASKS</promise>` sentinel is **informational only** and MUST NOT
  be a Strike input in its own right.

What a **Strike** counts depends on whether the Runner has a **Pickup** (§14.3), because only a
Runner that binds one issue per Iteration can have an **Attempt lifecycle** to charge from:

- **A Runner with a Pickup** (contract 1.27) MUST charge exactly **one Strike per issue this Run
  has given up on** — the issue's transition into the lifecycle's terminal `skipped` position
  (§14.3), charged once, at the ending that defeats it. An Iteration that made no progress MUST
  NOT record a Strike of its own, and progress MUST NOT reset the counter: the lifecycle is
  monotonic, so an issue an advance rescued was never given up on and an issue that was is not
  un-given-up-on by another issue's advance. `GIT_LOOPY_MAX_NMT_STRIKES` (default `3`) is
  therefore *how many issues this Run may abandon before it stops*, and reaching it ends the Run
  with exit `1` (§10, `stuck`).
- **A Runner without a Pickup** MUST keep the original accounting: an Iteration that made no
  progress records a Strike, `GIT_LOOPY_MAX_NMT_STRIKES` (default `3`) **consecutive**
  no-progress Iterations end the Run with exit `1` (§10, `stuck`), and progress resets the
  consecutive-strike counter.

`conformance/progress-strikes.json` forks along exactly that line: from fixture schema `2` a case
MAY carry a `distributions` selector naming the members whose accounting it describes, and a case
carrying none is family-wide. An adapter MUST run the cases naming its own distribution and MUST
NOT run the others.

## 7. Checkpoint (phase 1, MUST)

After accounting, if the working tree has any uncommitted **or** untracked changes, the
Orchestrator MUST stage everything (`git add -A`, honouring `.gitignore`) and make exactly one
**close-keyword-free** commit attributed to the **Active issue**, so the next Iteration starts on
a clean tree and no work is lost. A Checkpoint:

- MUST NOT contain a closing keyword (it must never auto-close an issue),
- MUST be excluded from Strike progress (§6) and from the run-summary commit tally,
- MUST warn-but-not-abort on failure (e.g. nothing to commit).

## 8. Auto-push (phase 1, MUST)

Immediately after the Checkpoint, whenever the Iteration produced **new commits** (agent commits
and/or the Checkpoint just authored), the Orchestrator MUST `git push` the current branch to its
configured upstream. Push failures — no upstream, unreachable/missing remote, auth failure, or a
non-fast-forward rejection — MUST **warn but never abort**, so a **local-only repo completes
normally**. An Iteration that produced no new local commits MAY skip the push.

## 9. Iteration cap (phase 1, MUST)

An optional positional argument `N` caps the Run at `N` Iterations. `0` or omitted means
unlimited. Reaching the cap is a **clean** exit (`0`, §10). A non-numeric argument is a usage
error (exit `2`).

## 10. Exit codes (phase 1, MUST)

| Exit | Meaning              | When                                                                 |
| ---- | -------------------- | -------------------------------------------------------------------- |
| `0`  | Clean — queue empty  | An Iteration's collection (§2) finds the Pool empty.                 |
| `0`  | Clean — cap reached  | The optional iteration cap `N` (§9) is reached.                      |
| `1`  | Aborted — stuck      | The `GIT_LOOPY_MAX_NMT_STRIKES` Strike ceiling is spent (§6).        |
| `1`  | Aborted — all skipped | A Pickup found the Pool non-empty and could bind none of it (§14.3). |
| `1`  | Waiting — all blocked | Every Pickup refusal proved an open native blocker (§3.3.1).         |
| `1`  | Aborted — preflight  | A required precondition failed before the first Iteration (§1), the Pool could not be read (§2.2), or an unread refusal left it unresolved (§3.3.1). |
| `1`  | Stopped — operator   | The operator ended the Run deliberately (§10.1, contract 2.3).       |
| `2`  | Usage error          | Malformed invocation (e.g. non-numeric iteration cap, §9).           |

A Runner with a **Pickup** (§14.3) MUST distinguish the two exit-`1` aborts by reason, and MUST
NOT report either as the exit-`0` empty queue: "there is nothing to do" and "I could not take any
of what there is" are different facts about the repository, and only the first is a finished Run.
The `all_skipped` reason is required from contract 1.27 because a Runner whose Strike counts
skipped issues no longer charges anything for an Iteration that binds nothing — so without a
terminal reason of its own, a Run every one of whose candidates is defeated would re-walk the
same Pool for as long as its Iteration cap allowed. A Runner without a Pickup never reaches this
reason and is not required to name it beyond mapping it (§10 is the family-wide termination
matrix that `conformance/exit-codes.json` pins for every member).

`all_blocked` is terminal on the same evidence: a Run cannot close a blocker without first
starting work, and no candidate can start. It deliberately shares exit `1` with `all_skipped`.
Both leave work unfinished, so an unattended caller must not treat either as the clean,
exit-`0` empty Pool; the distinct reason is the actionable branch for a caller that can wait for
dependency closure instead of repairing a refusal. `all_blocked` applies only when every skipped
candidate proves an open dependency. A mixed Pool remains `all_skipped`, so waiting never hides
work an operator can fix — except where the mix holds a refusal nobody could read, which §3.3.1
sends to `preflight_failed` instead. Both of these reasons are claims about the *work* in the
Pool, and neither may be established by a *read* that failed.

### 10.1 An operator Stop is a decided outcome (contract 2.3, MUST)

An Orchestrator that offers the operator a **Stop** MUST terminate it under the `operator_stop`
reason and its **non-zero** exit code. A Stop is a *decided* end to a Run — a human chose it, and
work was left unfinished — so it MUST NOT be reported as the exit-`0` empty Pool, and it MUST NOT
borrow the vocabulary of an exit nobody decided. A supervising script is never told everything was
fine (ADR-0024). An Orchestrator that offers no Stop never reaches this reason and is not required
to name it beyond mapping it, exactly as §10 requires of `all_skipped`.

The Stop itself takes **two stages** (ADR-0043), driven by the same gesture repeated:

1. The first latches a wind-down. Refill, new **Lane** reservations and new **Iterations** stop at
   once; every started contribution and **Integration** operation runs to completion and
   integrates. The latch is durable — a later publication MUST NOT resume refill, which is what
   distinguishes it from the drain a spent **Strike** ceiling latches.
2. The second cancels the agent sessions still running, **salvaging** each one's workspace as a
   **Checkpoint** first. Cancellation is *requested*, never awaited.

Cancellation stops at **round boundaries** — a Lane agent session, or a bounded auto-resolution
session inside an Integration cascade — and MUST NOT interrupt a publish transaction: the merge of
an already-verified stage, the issue closure, and the branch deletion. A Run torn open mid-publish
manufactures the one state nothing reconciles, and the transaction is seconds long.

A contribution or Iteration ended by the second stage is **visible and blameless**: it MUST produce
a **Summary** row, and it MUST feed neither the **Strike** counter nor **Demotion**. A human
pressing a key is not evidence against a **Routed pair**. No third, harder in-band verb exists; the
operating system already provides one, and salvage is what makes it safe.

## 11. Environment-variable surface (MUST honour the phase-1 core)

Resolution precedence across the family is **CLI flag > env var > project config > global config >
built-in default** (config tiers arrive in phase 3; phase 1 honours CLI + env + default).

| Variable                       | Phase | Default          | Meaning                                                        |
| ------------------------------ | ----- | ---------------- | -------------------------------------------------------------- |
| `GIT_LOOPY_MODEL`              | 1     | `claude-opus-5`  | Model id (bare base id).                                       |
| `GIT_LOOPY_REASONING_EFFORT`   | 1     | `max` for the built-in model | `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, or `max`; omitted and explicit `none` are distinct. A recognized model-id suffix is peeled into this field, and selecting another model without an effort leaves it omitted so the backend chooses. |
| `GIT_LOOPY_ISSUE_SOURCE`       | 1     | `github`         | `github` or `prds` (legacy local-markdown mode).              |
| `GIT_LOOPY_MAX_NMT_STRIKES`    | 1     | `3`              | Consecutive no-progress Iterations before abort.              |
| `GIT_LOOPY_INCLUDE_PRS`        | 3     | off              | `1`/`true`/`yes` to also advance `ready-for-agent` PRs.       |
| `GIT_LOOPY_INTERACTIVE`        | 2     | auto (TTY)       | MUST be honoured only by a member whose declared parallel capability manifest exposes this operator choice; Python still ignores it because terminal selection is structural: a TTY detaches the worker and keeps the parent as the attach client, while non-TTY stays on the direct line printer. |
| `GIT_LOOPY_MODEL_SELECT`       | 3     | off              | `1` enters the startup model picker (**ModelSelectionMode**). |
| `GIT_LOOPY_DENY_TOOLS`         | 1     | empty            | Denylist of tools (set *union* across config tiers).          |
| `GIT_LOOPY_DENY_SKILLS`        | 1     | empty            | Deprecated denylist of skills (set *union* across config tiers); subtracts only (§17). |
| `GIT_LOOPY_ENABLED_SKILLS`     | 3     | unset            | Exact replacement of the configured base **Skill policy** for one Run; an explicit empty value is a real empty policy (§17). |
| `GIT_LOOPY_SEND_TIMEOUT_SECONDS`| 1    | impl default     | Per-iteration agent send timeout.                             |
| `GIT_LOOPY_OTEL_ENABLED`       | 4     | off              | `1` enables OTLP export (or `OTEL_EXPORTER_OTLP_ENDPOINT`).    |
| `GIT_LOOPY_MAX_PARALLEL`       | 5     | `1`              | MUST be honoured only by a member whose declared parallel capability manifest exposes an operator-selected Lane count; Python refuses it because its Execution host declares the ceiling. |
| `GIT_LOOPY_WORKTREE_SETUP`     | 5     | none             | Per-worktree setup command for **Parallel mode**.             |

## 12. Event schema (phase 1, MUST)

Every Orchestrator MUST emit its structured record as JSONL using the shared **Event schema**
(`git_loopy.events`), so the **TUI helper**, the `.git-loopy/logs/<iso>-<run_id>.jsonl` replay
log, and any external consumer read one format regardless of which port produced it.
The additive Event schema has compatibility `schema_version` **1**; changing the Wrapper contract
does not implicitly change that version. Unknown event types and unknown payload fields remain
additive and MUST be ignored by compatible consumers.

Every line shares this envelope, with keys in a stable order (envelope keys first, then payload
keys sorted):

```json
{"ts": "2026-05-16T00:00:00.000Z", "run_id": "01HXR...", "iter": 3, "type": "...", "...": "..."}
```

The `type` **string literals** — not the constant names — are the contract, and they are pinned
here against `git_loopy.events`. Wrapper-emitted types (phase 1 core): `wrapper.run.start`,
`wrapper.run.end`, `wrapper.iteration.start`, `wrapper.iteration.end`,
`wrapper.afk_ready.collected`, `wrapper.commit.recorded`, `wrapper.checkpoint.recorded`,
`wrapper.push.recorded`, `wrapper.auto_close`, `wrapper.strike`, `wrapper.pr.advanced`,
`wrapper.ask_user.attempted`. Contract-1.5 addition within compatibility schema 1:
`wrapper.pool.excluded` — one per `ready-for-agent` candidate the discriminator rejected (§3.1),
carrying `issue`, `title`, and one `reason` from the closed exclusion vocabulary, emitted in source
order *before* the `wrapper.afk_ready.collected` it explains. That collection Event additionally
carries `excluded`, the count of exclusions, so a replay can tell an empty tracker apart from a
tracker whose every candidate was rejected. `wrapper.pool.excluded` is Run-scoped, never
contribution-scoped: it names work that never became a **Lane contribution**, so it carries the
collecting Iteration's `iter` and no contribution identity.
Dashboard Insight additions within compatibility schema 1 are `wrapper.issue.activated`,
`agent.output`, and `usage.context_window`; `wrapper.skill_policy.resolved` is the redacted
Run-scoped record of the frozen **Effective Skill policy** (§17). Rolling-dispatch additions
within compatibility schema 1 are listed under *Rolling-dispatch contribution lifecycle* below.
Producing these additive events is capability-dependent. TTY attach-client failures are local UI
failures only: they emit no special Event and do not change the worker's own Run record.

Contract-2.4 puts **Wind-down** on the wire. A Run emits
`wrapper.stop.requested` when it latches a drain or escalates it to cancellation:
`cause` is one of `operator_stop`, `strike_limit`, or `iteration_cap`; `stage` is
the ordered ladder `drain`, then `cancel`; and `draining` is the observed number
of contributions still in flight (`0` for a serial Run). Only `operator_stop` may
emit `cancel`. The Event records the true latch, not an input gesture, so each
transition emits once and a third Stop gesture emits nothing. A green publication
may clear only a Strike drain; that transition emits `wrapper.stop.lifted` with
`cause: "strike_limit"` and its observed `draining` count. Dashboard consumers
derive their stopped state from these Events: a trace that predates them is
unknown, and `wrapper.run.end` with `outcome: "interrupted"` is not a Stop.
Note the shape: each is dotted `wrapper.<noun>.<verb>`, with underscores used only *within* a
segment (`afk_ready`, `auto_close`, `ask_user`, `pr`, `work_finished`,
`branch_observed`, `recovery_started`, `refill_turn`), and two that are
two-part (`wrapper.auto_close`, `wrapper.strike`). SDK-mapped types (emitted when the port streams
SDK events): `session.created`, `session.idle`, `session.deleted`, `assistant.message`,
`assistant.reasoning`, `tool.call`, `tool.result`, `tool.permission_requested`,
`tool.permission_denied`, `usage.tokens`, `session.error`, `model.call_failure`. Secrets MUST be
scrubbed before a line is written. Ports
MUST copy these literals verbatim from `git_loopy.events`; a drifted literal (e.g. an underscore
where a dot belongs) is a conformance failure.

The last two carry the harness's own account of a failure — its error type, its message, the
service status code where one was reached, and for a call failure the source that failed —
rather than a sentence about it, which is what lets an Orchestrator tell an exhausted quota, a
rate limit, and a rejected credential apart from a session that merely produced nothing (#403).
Mapping them is recording, not reacting: an Orchestrator MUST NOT abort a **Run**, back off, or
withhold an issue on the strength of one. As with every SDK-mapped type, a port that streams no
SDK events declares the literals and emits neither.

Contract-1.21 addition within compatibility schema 1, and an **extension of an existing record**
rather than a new type: `wrapper.pickup.bound` carries the **Routing resolution** its Pickup
reached (§14) as `model`, `effort`, `context_tier`, `routing_source`, `task_type_keys`,
`gate_warnings` and `lifecycle_position`. `model` and `effort` are the family's existing
routed-pair vocabulary — the same two words a Contribution's `summary` already uses — so a
consumer reads a Pickup's pair the way it reads a Lane's. `routing_source` is spelled in full
rather than as `source` because the same payload's `reason` answers "why" in the unrelated Pickup
vocabulary (`order` / `priority` / `pin`), and one record cannot carry two differently-scoped
answers to one word. Every one of the seven is optional-when-present, so a Runner that implements
no routing emits the binding exactly as before and stays conforming; `null` is a *value* (the
backend chooses) and not an absence. `event_schema_version` does not move: the seven are additive
payload fields, which every schema-1 consumer already ignores when unknown.

Contract-1.24 addition within compatibility schema 1, and an extension of an existing record for
the same reason: `wrapper.run.start` carries the **Run readback** (§14) as `model`, `effort`,
`context_tier`, `escalation_rung`, `routes`, `unconfigured_task_type_keys`, `routing_suppressed`,
`harness_version`, `roster_cli_version` and `roster_diverged`. `escalation_rung` is an object —
`model`, `effort`, `configured_effort`, `gate_warnings` — or `null` where escalation is not in
force, and each entry of `routes` is that object plus the `key` it was configured under, spelled
exactly as Config spelled it. Two efforts travel on every pair: `effort` is the **gated** value,
what would actually be sent, and `configured_effort` is what Config supplied, because a readback
carrying only the gated one reports the outcome and loses the request an operator can correct.
That gating describes an unselected Route policy. Under either selected policy
(§14.3/§14.4), configured Static routes and explicit escalation are echoed unchanged;
the authenticated harness validates them, not the offline roster.
`roster_diverged` is three-valued — `true`, `false`, or `null` for an unreadable
`harness_version` — and a consumer MUST NOT read `null` as agreement. All ten are
optional-when-present: an Orchestrator that routes nothing emits Run start byte-identically and
stays conforming, and their absence means *this Runner does not route*, never a Config it failed
to report. `event_schema_version` does not move, for the reason it did not move at 1.21: a field
that **appears** breaks no pinned consumer, only one that **changes** does.

Every `wrapper.run.start` MUST carry the exact distribution `release_version`, numeric
`schema_version: 1`, and an
`insight_capabilities` object with at least these boolean keys:

```json
{
  "agent_output": true,
  "structured_agent_events": true,
  "token_usage": true,
  "context_window": true,
  "skill_consultation": true,
  "cost": true,
  "routing": true
}
```

The values above are the Python Orchestrator's current manifest. The shell and PowerShell
Orchestrators declare only `agent_output` available. Later work may change a value to `true` only
when that Orchestrator emits the signal truthfully. `false` means unavailable. `true` with no sample
yet is still unknown. Unknown scalar values are JSON `null`; an observed count of none is `0`, and
an observed collection with no members is `[]`.

`routing` is the contract-1.25 addition (#411) and declares whether this Orchestrator reports the
**Routed pair** a unit of work runs on — the **Routing resolution** §14 obliges on a Pickup, and the
**Run readback** on Run start. It is per-distribution rather than run-scoped because a Run that pins
`--model` does not go silent: its Pickups still publish a resolution, sourced
`defaulted_explicit_override`, so two Runs of one binary cannot differ on the answer. A port that
resolves no pair MUST declare it `false` rather than omit it, for the reason a port that reads no
model listing declares `rate_card: false`: an omitted key leaves a **Dashboard** unable to tell
*this Orchestrator never routes* from *no Pickup has resolved a pair yet*, and a Route column that
cannot tell them apart reads as pending forever.

### Run-scoped Insight capabilities

The seven keys above are **per-distribution**: they answer "can this Orchestrator observe it at
all?", so they are the same for every Run of one binary and every port MUST declare all seven. A
**run-scoped** capability answers a different question — "did *this* Run obtain it?" — and two Runs
of one binary can differ. Run-scoped keys are declared in the same object, are **accepted from any producer and
required of none**, and are listed under `insight_capabilities.run_scoped` in
`conformance/event-schema.json`.

There is one today, `rate_card` (ADR-0026). An Orchestrator that declares it MUST also carry a
`rate_card` key on `wrapper.run.start`: the **Rate card** it resolved, or `null` when it resolved
none. The card is the harness's own live per-model price listing, obtained from the same
`models.list` call that supplies the roster and the picker's premium column, resolved **once** at Run
start and held fixed for the whole Run, so every row of one **Summary** is denominated identically
even if the server reprices mid-Run. It MUST NOT be loaded from a packaged file — a pinned fixture
cannot be correct under `COPILOT_CLI_PATH` (ADR-0019).

The card is **provenance, not arithmetic**. No figure anywhere in the kit derives from it, so an
absent card never costs a figure and never affects the separate `cost` declaration. Its prices are
denominated in **AI Credits per batch of `batch_size` tokens** — the same unit the harness already
reports as *billed* — and MUST be recorded as published: separate input, output, cache-read and
cache-write prices, the batch size, the prompt-token budget and the nested `long_context` block.
Flattening them to one rate is a conformance failure: the cache prices dominate a real agent loop,
and a card recorded lossily is not a record of what a Run was billed under.

A card that cannot be fetched MUST NOT prevent a Run from starting. The Run warns on exactly the
terms the roster fetch failure already uses, declares the capability `false`, and proceeds.

```json
{
  "insight_capabilities": { "cost": true, "rate_card": true },
  "rate_card": {
    "models": {
      "claude-haiku-4.5": {
        "multiplier": 0.33,
        "discount_percent": null,
        "prices": {
          "batch_size": 1000000,
          "input_price": 0.1,
          "output_price": 0.4,
          "cache_read_price": 0.01,
          "cache_write_price": 0.125,
          "max_prompt_tokens": 128000,
          "long_context": null
        }
      }
    }
  }
}
```

All three Orchestrators declare `rate_card` (#334). The shell and PowerShell Orchestrators subscribe
to no SDK event stream and read no model listing, so they resolve no card on any Run: each declares
the capability `false` and publishes `rate_card: null` beside it. They declare it rather than omit
it because an omitted key leaves a **Dashboard** unable to tell *this Orchestrator cannot report a
rate* from *this Run's prices failed to load*, which is the collapse a separate declaration exists
to end. A port that declares it `false` MUST hold no price data and MUST fetch nothing.

Every `wrapper.run.start` MUST also carry a `parallel_capabilities` object with exactly these
boolean keys:

```json
{
  "parallel_mode": true,
  "rolling_dispatch": true,
  "integration_backlog": true,
  "adaptive_lane_limit": true,
  "contribution_events": false
}
```

`insight_capabilities` says what an Orchestrator can *observe*; `parallel_capabilities` says what it
can *schedule*. The values above are the Python Orchestrator's current manifest; the shell and
PowerShell Orchestrators declare every key `false`. `parallel_mode` is whether the Orchestrator can
fill more than one **Lane** at a time, `rolling_dispatch` whether it refills them continuously
toward the **Lane cap** rather than behind a barrier, `integration_backlog` whether it admits
finished Lane branches to the bounded backlog described below, `adaptive_lane_limit` whether its
**Effective Lane limit** reacts to **Pressure signals**, and `contribution_events` whether it emits
the **Lane contribution** lifecycle stream. Python declares `contribution_events: false` today
because those literals are reserved and have no producer: a Parallel Run still records legacy
**Wave**-shaped rows, and advertising a stream no replay contains would be the same lie as reporting
an unavailable counter as `0`.

`parallel_mode: false` is not one `false` among five. Refill, the backlog, adaptation, and the
contribution stream all presuppose Parallel mode, so an Orchestrator that declares `parallel_mode`
unavailable MUST declare every other parallel capability unavailable with it. It MUST additionally
**refuse** a requested Lane cap above 1 at preflight, naming the unsupported capability, the
distribution that cannot honour it, and the setting the operator can change — a refused Run exits
with the preflight-failure code (§10). Accepting the cap and running serially is forbidden: a
silently serial Run is byte-identical to a Parallel Run whose tracker carries no `parallel-safe`
issue, so the operator cannot tell an unimplemented feature from an unlabelled backlog.

**Execution hosts (phase 5, contract 2.2).** The same manifest MUST carry an
`execution_hosts` list alongside its booleans. Its entries come from the closed
family vocabulary of host placements relative to the Orchestrator; they never
name an isolation grade. The list is present even when empty. A non-empty list
implies `parallel_mode: true`, and `parallel_mode: false` requires
`execution_hosts: []`. The Python Orchestrator currently declares `["local"]`;
the shell and PowerShell Orchestrators declare `[]` because they schedule no
**Lane**. A requested host absent from the distribution's list MUST be refused
at preflight with the preflight-failure exit code and a diagnostic naming
`execution_hosts`, the distribution, and the setting an operator can change.
This is a distribution capability refusal, not a Continuation capability path.

An Orchestrator that emits **Lane contributions** MUST announce its selected
**Execution host** on `wrapper.run.start` as an `execution_host` object carrying
`placement`, `isolation_grade`, declared `capacity`, and `starting_lane_limit`.
It MUST stamp the selected placement as `host` on every
`wrapper.contribution.start`, including `local`; the isolation grade is announced
once per Run and MUST NOT be repeated per contribution. Shell and PowerShell emit
neither payload because they emit no contribution lifecycle Event. Consumers MUST
read an absent declaration or stamp in a historical trace as `unknown`, never as
an inferred local placement. These are additive payload fields within Event schema
compatibility 1; no envelope key or Event-type literal is added.

**A truthful `parallel_mode: true` can still yield a wholly serial Run, and it MUST say so
(contract 1.28).** The rule above is about the *distribution*; an Orchestrator that declares
Parallel mode truthfully may still meet a Run whose **issue source** has no **Parallel-safe**
concept and can therefore never offer **Lane** work at all. Refusing the Lane cap there would be
wrong — the serial fallback works the same issues to the same outcome and strands nothing — but
staying silent reaches the same collapse by the other road, since the Run is again byte-identical
to a serial one under a banner naming a Lane cap. Such a Run MUST emit exactly one Run-scoped
`wrapper.parallel.degraded` carrying `reason`, `lane_cap` and `issue_source`, immediately after the
`wrapper.run.start` it qualifies, and MUST reach the operator's own output with it. `reason` comes
from a closed vocabulary — `source_not_rolling_capable` today — and the record is **not**
`wrapper.parallel.serial_fallback`: that one reports a single **Iteration** a Lane-capable Run
worked because the **Pool** happened to hold no eligible candidate, it is fixed by triage, and it
recurs; this one is a property of the Run, is fixed by nothing inside it, and is stated once.
`parallel_capabilities.parallel_mode` stays `true` throughout: the manifest describes the
distribution, this record describes the Run.

The following additive Insight payload shapes are reserved by schema 1. Existing Phase 1 traces,
including payload-free `wrapper.iteration.end` records, remain valid. When an Orchestrator begins
emitting or enriching one of these records, it MUST use the pinned shape; the downstream
Orchestrator rollout tickets own enabling those producers.

- `wrapper.issue.activated`: `issue`, UTC RFC3339 `activated_at`, and `binding_source`. Once
  produced, one event authoritatively and immutably binds an Iteration to its Active issue.
  A serial Iteration binds at **Pickup** with `serial_pickup` (§3.3); `working_marker`,
  `closure`, `commit`, and `single_member_pool` remain in the vocabulary because a stream
  recorded before contract 1.12 carries them and MUST still replay. Parallel Lane pickup uses
  `lane_pickup`. A later marker or fallback never replaces the first binding. Output and
  Consumption observed before the event remain pending and are attributed
  when the event arrives. A record whose `activated_at` is absent or is not a resolvable instant
  is not a valid activation: it MUST NOT bind, because binding it would republish a
  non-RFC3339 `first_started_at` on every later Iteration end. The Iteration reports no issue
  contribution and the Run continues.
- `wrapper.pickup.bound` and `wrapper.pickup.skipped` (contract 1.13): `issue`, `reason`,
  `position`, and `considered` — which issue, why, where it sat in the order, and how long the
  order was. Both are **Run-scoped**: they carry no `contribution_id` and no `lane_id`, because a
  Lane's contribution identity is minted when its session starts and a Pickup happens before
  that — an Event that demanded the identity triple could never be emitted at the moment it
  describes. `reason` on a binding is one of `order`, `priority`, or `pin` — `pin` exactly when
  §3.2's **Pin** named the bound candidate, which outranks a `priority` label on the same issue;
  on a skip it is the free-text reason the candidate was passed over. `considered` is required rather than derivable:
  *the runner took the oldest* and *the runner took the only one left* are different facts about
  a backlog, and `position: 1` alone cannot tell them apart. Every skip that ended in a binding
  MUST precede that binding, which is the same ordering `wrapper.pool.excluded` already keeps
  against `wrapper.afk_ready.collected`. A stream recorded before contract 1.13 carries neither
  record and MUST still replay: a consumer that requires them to reconstruct a Run is reading a
  guarantee this schema does not make.
- `agent.output`: `text` and `kind`, where the only schema-1 kind is `unclassified`. Once produced,
  native CLI text MUST NOT be relabeled as SDK reasoning, assistant, tool-call, or tool-result
  data.
- `usage.context_window`: `current_tokens`, nullable `token_limit`, nullable
  `effective_target_tokens`, and nullable `effective_ceiling_tokens`.
- An enriched `wrapper.iteration.end`: `outcome`, monotonic `duration_seconds`, normalized
  `summary`, and an `issues` contribution list.

The normalized `summary` requires `model`, `tokens_in`, `tokens_out`, `observed_tokens`,
`tool_count`, `skill_call_count`, sorted-distinct `skills_consulted`, `commits`,
`auto_closures`, `pr_advances`, `strikes`, and nullable `peak_context_window`. Each issue
contribution requires `issue`, `status`, UTC RFC3339 `first_started_at`, closure-only `closed_at`,
closure-only `issue_elapsed_seconds`, `active_seconds`, `cumulative_active_seconds`,
`consumption` (`model`, `tokens_in`, `tokens_out`), and nullable `peak_context_window`. Only
authoritative source closure populates closure-only fields.

Cost is the harness's reported billing — optional `credits`, `premium_requests`, `cache_read` and
`cache_write`, added additively (ADR-0026). They are optional rather than required precisely so an
Orchestrator that cannot observe billing omits them rather than fabricating a figure. The
dollar-named `cost_usd` is **retired**: it was git-loopy's own list-price estimate, the price table
is deleted, and it is never repurposed to carry Credits — a consumer must never read Credits out of
a key whose name says dollars. Producers that still emit it remain conformant; consumers ignore it.

The shell and PowerShell Orchestrators emit this normalized payload from their native observable
boundaries. Iteration, Active-issue, and cumulative Active durations come from each Orchestrator's
monotonic clock; agent commits, successful wrapper closures, PR advances, and Strikes remain
observed counts. Model and token Consumption, structured tool and Skill activity, Context fill,
and Cost remain `null` because native Copilot CLI output does not expose those
measurements. The configured model is not a substitute for observed Consumption, and unavailable
counters or collections MUST NOT be reported as `0` or `[]`.

Envelope and nested timestamps MUST be RFC3339 UTC with a trailing `Z`. Durations MUST be
non-negative seconds measured from a monotonic clock; renderers MUST NOT derive them by
subtracting wall-clock timestamps.

### Rolling-dispatch contribution lifecycle

**Rolling dispatch** (Parallel mode) has no barrier round, so a Parallel record belongs to a
**Lane contribution** rather than to an **Iteration**
([ADR-0020](adr/0020-rolling-dispatch-with-bounded-green-integration.md)). These additive type
literals are reserved within compatibility schema 1. Contribution lifecycle:
`wrapper.contribution.start`,
`wrapper.contribution.work_finished`, `wrapper.integration.parked`,
`wrapper.integration.admitted`, `wrapper.integration.started`,
`wrapper.integration.branch_observed`, `wrapper.integration.recovery_started`,
`wrapper.integration.published`, and `wrapper.contribution.end`. Scheduler-scoped:
`wrapper.pool.refreshed`, `wrapper.concurrency.changed`, `wrapper.serial.requested`,
`wrapper.pipeline.quiescent`, `wrapper.rolling.refill_turn`,
`wrapper.parallel.serial_fallback`, and `wrapper.parallel.degraded`.

- **Identity, not Lane.** Every contribution-scoped record MUST carry `contribution_id`, `issue`,
  and `lane_id`, and its envelope `iter` MUST be `null`. `lane_id` is the reusable **Lane** the
  contribution *started* in and never changes, because a Lane is refillable the moment its
  contribution is admitted to **Integration** — a record identifying only its Lane becomes
  unattributable as soon as the next contribution starts there. Consumers MUST NOT rely on a
  mutable Lane→issue lookup.
- **Stamped existing records.** A Lane's ordinary records — `assistant.*`, `tool.*`,
  `usage.tokens`, `usage.context_window`, `agent.output`, `wrapper.commit.recorded`,
  `wrapper.checkpoint.recorded`, `wrapper.auto_close` — carry the same triple when they belong to
  a contribution. The same literals remain valid, unstamped, for serial Iterations.
- **Scope separation.** A Lane contribution MUST NOT emit `wrapper.iteration.start` or
  `wrapper.iteration.end`; a serial Iteration keeps both and its positive `iter`.
- **`wrapper.contribution.end` is the finalized Parallel row and the Strike transition.** Its
  `reason` MUST at least distinguish `published`, `unchanged_branch`, `checkpoint_failed`, and
  `serial_fallback`; only `published` is Parallel progress. A publication whose runner-driven
  closure has not yet verified is *not* a contribution end. Lane-work and recovery Consumption and
  commits appear exactly once, in the originating contribution, and runner **Checkpoint** commits
  stay out of the commit total.
- **Unknown stays unknown.** `wrapper.concurrency.changed` reports the immutable configured Lane
  cap and the current effective limit, and reports a signal the Run cannot observe as `null` —
  never an estimate and never `0`. It is emitted for an authoritative transition, not per
  observation.
- **Legacy traces.** Historical **Wave** logs carry `lane_issue` and no contribution identity.
  They remain readable and MUST NOT be reinterpreted as contributions.
- **The backlog is bounded, and the bound is the whole point.** **Integration** is one serialized
  stage, and the **Integration backlog** it consumes has a high-water mark of exactly **two** — one
  contribution integrating plus one waiter. A third finisher emits
  `wrapper.integration.parked`, keeps its **Lane** occupied, and waits; admission is FIFO by
  finish order, broken by ascending issue number, and a parked contribution enters the backlog on
  `wrapper.integration.admitted`. That admission — not publication — is what frees the Lane for
  refill, which is why a record identifies its contribution and not its Lane. A full backlog is
  **Integration backpressure**: **Rolling dispatch** stops *starting* new Lane work while it holds,
  and resumes the moment a slot frees. It is a refill bound, never a pause — Lanes already running
  finish normally and nothing is cancelled. This is what makes the **Lane cap** a ceiling rather
  than a utilization promise, so a Run that never reaches its cap is not thereby faulty.
- **Integration is gated privately and recovery is bounded.** Each contribution is merged into its
  own private **Integration stage** against the latest published green base and re-runs the feedback
  loops *there*, so a red or conflicting result is never observable on the base branch and there is
  nothing to undo. `wrapper.integration.branch_observed` reports how many publications landed since
  that branch was cut, or `null` when the Run cannot observe it. A conflicting or failing
  contribution gets bounded runner-driven recovery in the same stage: each attempt emits
  `wrapper.integration.recovery_started` with its `attempt` and the immutable `max_attempts`, and
  attempts MUST NOT exceed three. Recovery Consumption and commits are counted once, in the
  originating contribution. Persistent failure ends the contribution unpublished rather than
  publishing something the loops did not pass.
- **A Run that requested Parallel mode says so.** An Orchestrator that implements Parallel mode
  SHOULD carry `parallel_mode`, `lane_cap`, and `effective_lane_limit` on `wrapper.run.start`, and
  MUST emit `wrapper.parallel.serial_fallback` once per serial **Iteration** it works because it
  found no eligible **Parallel-safe** candidate — with the eligible count and a `reason` from the
  closed vocabulary `no_parallel_safe_candidates`, `all_parallel_safe_worked`,
  `parallel_safe_unavailable`. Eligibility is a human assertion the runner never infers, so the
  reason MUST reach the operator's own output and not only the Event stream; otherwise a Run whose
  tracker carries no `parallel-safe` issue is byte-identical to a serial Run and reads as a broken
  flag. A serial turn granted while eligible Lane work remains is interleaving, not a fallback, and
  emits nothing. A Run that did not request Parallel mode emits none of these. A Run that
  requested Parallel mode and could never fill a **Lane** at all additionally emits
  `wrapper.parallel.degraded` exactly once — see the `parallel_capabilities` rules above, which is
  where refusing and degrading are told apart.

The `contribution_identity` and `payload_contracts` sections of
[`event-schema.json`](../git-loopy/conformance/event-schema.json) pin this vocabulary, its
`rolling_stream_cases` pin whole ordered streams — Lane refill after admission, parking against a
full backlog, bounded recovery, the serial latch, and a Parallel Run that never engaged — and the
serialization cases pin the wire form. A distribution named on a stream's `distributions` list is
obliged by that stream according to its role: a producer MUST drive the stream through its own
production serializer and match the pinned lines; a consumer MUST fold the stream without
diagnostics. The three Orchestrator suites carry their unchanged producer obligation, including
when an Orchestrator cannot *produce* a rolling record and must still read and write the same
bytes. The Dashboard core carries the consumer obligation and emits no Event. Its
`parallel_capabilities` section pins each Orchestrator's manifest. As with the other reserved
Insight shapes above, producing these records is capability-dependent and the rolling-dispatch
Orchestrator tickets own enabling the producers; the Event-schema fixture revision advances with
the first Orchestrator that emits them, since that revision is what a distribution's capability
manifest advertises.
[`docs/parallel-mode.md`](parallel-mode.md) is the operator-facing companion to this section.

### Calibration records (contract 1.16, Python-only)

A **Calibration** buys **Trials**, and a Trial contains an agent session that anywhere else in
git-loopy would be an **Iteration**. It deliberately is not one
([ADR-0027](adr/0027-routing-is-calibrated-by-measurement.md)). Iterations are attributed to a
**Run** and tick the **Strike** counter, and that counter is shared and consecutive — reaching
the limit ends the Run. A Trial belongs to a Calibration; an Iteration belongs to a Run.

These additive type literals are reserved within compatibility schema 1: `calibration.trial.start`
and `calibration.trial.end`. Like the **measured tier** they serve they are **Python-only**
(§14.1); the shell and PowerShell Orchestrators declare the literals so the vocabulary stays whole
and never emit them.

- **No Run, and the record says so.** Every Calibration record — the lifecycle pair *and* the
  ordinary records a Trial's session writes (`assistant.*`, `tool.*`, `usage.tokens`,
  `usage.context_window`, `agent.output`) — MUST carry `run_id: null`, a null `iter`, and the
  identity pair `calibration_id` / `trial_id`. `run_id: null` is what keeps a Trial's
  **Consumption** out of a Run's Cost totals and stops a consumer rendering a phantom Run.
- **`trial_id` is per Trial, not per Proving task.** A Calibration legitimately runs the same
  **Proving task** at several rungs, which the task pin alone could not separate.
- **Scope separation.** A Calibration MUST NOT emit `wrapper.run.start`, `wrapper.run.end`,
  `wrapper.iteration.start`, `wrapper.iteration.end` or `wrapper.strike`. It produces no Run
  summary row, no **Queue** entry and no Iteration number, and it never ends or aborts a Run.
- **An interrupted Trial leaves a `start` with no `end`.** It produced no measurement, and a
  synthesised result would put a Trial the search never scored into the record.
- **Consumers tolerate a Calibration-only stream.** A **Dashboard** or replay reader MUST handle
  a stream carrying no Run lifecycle records at all, and MUST NOT adopt a Calibration record's
  identity as a Run's.

### Renderer-neutral Dashboard seam

The language-neutral
[`dashboard-insights.json`](../git-loopy/conformance/dashboard-insights.json) fixture is the
semantic boundary between Orchestrators and live-interface implementations. It supplies normalized
Event prefixes plus injected render time, display-zone input (a fixed UTC offset in these
deterministic fixtures), and configured Run facts, then pins the
expected toolkit-neutral Dashboard and per-issue drill-in model.

The Dashboard inventory is `Header -> Queue -> Activity -> Summary`. The per-issue drill-in is
`detail header -> Iteration breakdown -> Log`. Queue columns are ordered `Issue | Status | Started
| Active | Closed | Iters | Route | Tokens in | Tokens out | Credits | Premium`.
Iteration-breakdown columns are ordered `Contribution | Outcome | Duration | Status | Active |
Route | Tokens in | Tokens out | Cache read | Cache write | Credits | Premium | Peak Context fill`,
where `Outcome` and `Duration` are the owning Iteration's own disposition and monotonic duration
while `Status` and `Active` remain scoped to the issue within that contribution. `Route` is the
**Routing resolution** the issue's Pickup reached — model, effort and source — and it is the one
column whose two tables answer different questions: the Queue row carries the newest resolution
the issue has (what it costs now), while a contribution row carries the one resolved while that
contribution was open and `null` where none was, so an escalated issue reads as a change between
rows and no pair is ever back-dated onto a contribution that did not run on it. `Cache read` and
`Cache write` are components
of `Tokens in` rather than figures beside it, so no total sums them in; they are drill-in detail
and reach no Queue or Summary column. Context
fill is current-Iteration scoped; Queue accounting and the Log aggregate one issue across
contributions; each Summary row is one Iteration or Lane contribution; and the Iteration breakdown
is the same ordered contribution set counted by `Iters`.

An unavailable value projects to an em dash, while observed none remains `0` or `[]`. A
capability an Orchestrator declares unavailable at Run start arrives as a `null` normalized
measurement, and renderers MUST project it as unknown rather than as an observed `0`, `[]`, or a
substituted configured value — including a contribution whose whole `consumption` record is
unavailable. Renderers localize UTC timestamps from the supplied display-zone rules at each
timestamp's own instant, preserving historical and daylight-saving offsets and monotonic durations.
The presentation boundary resolves the **Viewing machine**'s **Display zone**, not the Run's
Execution host: `TZ` takes precedence when supplied, otherwise the platform's native zone
configuration applies, including Windows historical timezone rules. The pure Dashboard core
receives those rules and MUST NOT read the environment or host clock.

Every human-facing timestamp follows that same rule, including routing preparation, expiry,
reuse, and evidence provenance in projected fields and Log text. Detailed provenance retains its
date and numeric UTC offset; compact clocks keep their existing format. Stored Events, replay
records, machine-readable Run evidence, durations, ordering, and artifact identities remain
canonical and unchanged. A display projection may carry localized timestamps without rewriting
its underlying evidence.

Normal launch and Attach MUST preserve the viewer's timezone environment (`TZ` and `TZDIR`)
and MUST NOT inject a sampled fixed offset. An explicit fixed-offset override, including zero,
remains authoritative for an operator or deterministic fixture. If zone resolution fails, the
interface remains usable but MUST diagnose the failure and label its UTC fallback; UTC MUST NOT
silently masquerade as local time. These presentation rules add no timezone-bearing Event field.

This rule binds every renderer surface for a Run, not just the live band: the
per-Iteration frozen artifact and the run-end totals artifact project the same unknowns, and a
cumulative total is unknown only when every completed Iteration in it declared that measurement
unavailable.
Glyphs, colors, widths, responsive truncation, keybindings, and toolkit widget structure are not
contractual. Future renderer issue #143 MUST consume this seam rather than redefine its inventory
or semantic meaning.

#### Per-Orchestrator obligations at this seam

The fixture's `semantic_contract` is the single declaration of what a Dashboard *is*. Its
`projection_fields` inventory names every field of every band, its `queue_columns` and
`iteration_breakdown_columns` carry a `fields` mapping from each rendered column onto that
inventory, and `binding_sources` groups the activation vocabulary into `marker`, `serial`,
`retroactive`, and `lane`. A renderer or Orchestrator MUST NOT keep a second copy of any of these
lists.

Every Orchestrator MUST:

- Emit `wrapper.issue.activated` with a `binding_source` drawn from the declared vocabulary. A
  `retroactive` source (`closure`, `commit`, `single_member_pool`) means the Iteration was already
  running when the evidence appeared, so the issue's Active stint opens at the *Iteration* start and
  the pre-marker output belongs to that issue. **Every other source opens the stint at the binding
  itself**, and an implementation MUST decide that by testing membership of the closed `retroactive`
  group rather than by naming the prospective ones — the complement is open, so a source added later
  is prospective by default, which is what a **Pickup** and a marker both are. A `lane_pickup`
  binding never becomes the single serial Active issue.
- Treat the first authoritative binding in an Iteration as final. A later `wrapper.issue.activated`
  naming a different issue is ignored, not a rebinding.
- Declare each Insight capability once at Run start and stay consistent with it: a capability
  declared unavailable MUST arrive as `null` in every normalized measurement it feeds, and a
  capability declared available MUST NOT use `null` to mean an observed zero or an observed empty
  collection.
- Derive `duration_seconds` and every `*_active_seconds` field from the monotonic clock, so a
  wall-clock adjustment moves the rendered timestamps without moving any duration.
- Report `first_started_at`, `closed_at`, and `activated_at` as RFC3339 UTC with a trailing `Z`;
  localization to the display zone is the renderer's job, not the producer's.

The native ports carry one additional obligation. A Dashboard case whose Run start declares the
native capability manifest MUST carry a `producer_rollups` entry for *every* `wrapper.iteration.end`
in the case, naming both native distributions and the producer facts behind that Event. The shell
and PowerShell Event-schema suites rebuild those payloads through their real Iteration-rollup seams
and compare them against the Event the Python reducer consumes, so a native trace in this fixture is
one both native rollup seams actually produce rather than a hand-written approximation. Shell rollup
arithmetic is integral, so a native case MUST NOT pin a fractional `duration_seconds`.

The probe's depth is the rollup seam, not the whole Run loop: it proves the payload is *producible*,
not that today's native Run loop reaches every input it accepts. The shell and PowerShell Run loops
currently pass a constant zero for PR advances and only an empty or `aborted` terminal outcome, so a
native `pr_advances` or `gone` Iteration is seam-reachable but not yet loop-reachable there.

The boundary with #143 is deliberate and narrow: this seam fixes inventory, ordering, scope,
nullability, placeholder meaning, and localization. It fixes nothing about renderer lifecycle,
process model, threading, redraw scheduling, input handling, or widget toolkit — no Rust or TUI
lifecycle belongs here.

## 13. Conformance (phase 1, MUST)

Each Orchestrator MUST pass the language-neutral fixtures in the
[Conformance suite](../git-loopy/conformance/README.md) (`git-loopy/conformance/`):

- **Discriminator** — bodies that do / don't carry both required headings (§3).
- **Selection order** — the total order over eligible issues: `priority` rank, `created_at`
  ascending, issue number as the tie-break that makes it total, plus the timestamp defects an
  undated issue is reported with (§3.2).
- **Close-keyword regex** — a corpus of matching and non-matching commit messages, the pool
  whitelist, issues-only, and first-encounter dedup (§5).
- **Progress / Strike accounting** — scenarios mapping (agent commits, closures, checkpoints,
  PR advances) → progressed? / strike? (§6).
- **Checkpoint message** — the runner-authored subject/body/trailer per Active issue, its
  close-keyword freedom, and its detectability (§7).
- **Exit-code table** — the input → exit-code matrix of §10.
- **Task-type taxonomy** — the closed set of permitted `task-type:` keys, and the refusal an
  unknown key meets: never a warn-and-default, and the refusal names the value and the permitted
  keys (§14).
- **Measured routing precedence** — the measured tier beside both Config scopes and an explicit
  override, and which measured statuses supply a **Routed pair** (§14.1).
- **Event schema** — exact type literals and envelope-first, sorted-payload JSON serialization
  (§12).
- **Dashboard Insights** — normalized Event prefixes and expected renderer-neutral Dashboard and
  drill-in projections, including inventory, Queue order, scopes, placeholders, and localization
  (§12).
- **Skill policy** — base-scope selection, explicit empty policy, environment replacement, Run
  overlays, disable-wins, legacy subtraction, Minimal fallback, the four validation failures, and
  the redacted resolved-policy projection (§17).

The suite is the generalized successor to the cross-runner parity test ADR-0002 deleted. A
conformance fixture change is the canonical way to evolve the contract.

### 13.1 Every fixture is claimed or waived (contract 2.0, MUST)

Every fixture in `git-loopy/conformance/` MUST be **accounted for** by every member of the
**Runner family** — the Python reference Orchestrator, the shell and PowerShell Orchestrators, and
the Rust **Dashboard** core. Each (fixture, member) pair carries exactly one verdict:

- **Claimed** — the member's own suite reads that fixture's bytes as it runs, takes an assertion's
  expected value out of them, and compares it against what the member's *production* seam returns.
  The test is falsifiable: mutate an expected field one of that member's asserted cases reads, and
  that member's suite goes red. A filename in a README, a doc comment, or production code no test
  drives is a *mention*, and a mention claims nothing.
- **Waived, out of scope** — this contract or an accepted ADR puts the fixture's decision outside
  the member's role (a packaging fixture for an Orchestrator that ships no packaging channel). A
  reason, and nothing else.
- **Waived, owed** — the member should exercise the fixture and does not. A reason **and** a
  tracking issue, so the gap is visible as debt. "Nobody has got to it" is always this kind, never
  out of scope, and where this contract is silent the entry is owed until a decision says
  otherwise.

Silence is not a fourth verdict. An unaccounted fixture is a **conformance failure**, not an
absence: the suite's green means "every member was asked and agreed", and a fixture one member
reads and three ignore produces that same green from a question only one member was asked.

The verdicts live in one register, `git-loopy/conformance/fixture-claims.json`, which is itself
Conformance data rather than a fixture and so carries no entry of its own. The **Python suite alone**
reads it — the Integration gate covers the family, and four implementations of one static
completeness check is four ways for the family to disagree about the file that records the family's
agreement. The other three members MUST NOT be required to read it. **No tree is green that
contains a fixture without a verdict for every member.** Verdicts should therefore land in the same
change as the fixture; what is gated is the merged result, not the shape of the change. See
[ADR-0049](adr/0049-every-conformance-fixture-is-claimed-or-waived-by-every-member.md).

## 14. Per-issue model routing (phase 3, MUST)

Wherever an Orchestrator binds an Iteration to a **single Active issue at pickup** it MUST
resolve the model and reasoning effort **from that issue's labels**, not from the frozen
run-wide default:

- **Read, never infer.** Read the routing key off the issue's `task-type:<key>` labels; the
  `task-type:` prefix is the contract. The Orchestrator MUST NOT infer the type from the title,
  body, or any other heuristic, and MUST ignore non-`task-type:` labels.
- **Resolve to one record.** Resolve the labels to a single **Routing resolution**: the gated
  model, reasoning effort and run-level context tier; the raw `task-type` keys exactly as read;
  every gate warning; a **Routing source**; and the attempt's lifecycle position. The source
  vocabulary is closed to `routed`, `defaulted_no_task_type_label`,
  `defaulted_unknown_task_type_key`, `defaulted_conflicting_task_type_keys`,
  `defaulted_explicit_override` and `escalated` — the set `routing-resolution.json` states as
  `routing_sources`. Source and lifecycle position are **separate axes**: an escalated retry has
  source `escalated`, while a same-pair crash retry keeps the source it resolved with and moves
  only its lifecycle position.
- **Select the pair.** Resolve the labels to a single `(model, effort)` via the shared
  `[routing]` config, honouring the family precedence spine (§11): `[routing]` is a
  **config-file-only** tier that replaces the *single global default* with a per-issue-type
  default — never a flag/env tier — and any explicit `--model` / `--reasoning-effort` (flag or
  env) suppresses routing run-wide (source `defaulted_explicit_override`). The taxonomy is closed
  to `planning`, `review`, `implementation`, `test`, `docs`, `chore`, and `bugfix` — the set
  `routing-resolution.json` states as `task_type_taxonomy`, matched exactly (a recased key is
  unknown): an unknown `task-type:` key is refused, naming the value and the permitted keys.
  Suppressing routing run-wide does **not** excuse the refusal — an unknown key is refused before
  the routing map is consulted. Selection among valid labels is fixed, and a key the table
  **omits** resolves to the global default *as a value* rather than short-circuiting the
  comparison: no label is `defaulted_no_task_type_label`; keys whose resolved values agree use
  that pair — `routed` when the table configures every one of them, `defaulted_unknown_task_type_key`
  when it omits any; ≥2 keys resolving to different pairs fall back to the global default under
  `defaulted_conflicting_task_type_keys`. Only that last case warns, because it is the only one
  where the operator's own labelling is ambiguous. The keys are carried on the record
  **unnormalised**, exactly as the tracker spelled them, so a readback shows what arrived rather
  than what was inferred.
- **Gate and fall back.** Pass the resolved effort through the shared effort gate against the
  model roster and apply the fallback (an effort the model does not accept drops to "let the
  backend pick"; an unknown model passes through). Then gate the run-level **context tier**
  against the resolved model, downgrading a tier the model does not offer to `default`
  ([ADR-0017](adr/0017-context-tier-and-live-context-gauge.md)). Routed, default **and**
  escalated settings are gated identically, and every gate signal is kept **on the record**
  instead of being discarded. A **reasoning-incapable** model — one whose roster entry is the
  empty set — is therefore **unroutable**: an Orchestrator MUST NOT ship a recommended pair
  naming one, because an effort supplied to such a model *hard-rejects session creation* rather
  than downgrading, and a pair the gate can only rescue is a pair that fails wherever the gate is
  not in the path.
- **Pass to the single invocation.** Feed the gated `(model, effort)` to that Iteration's one
  `--model` agent invocation (§4), reusing the same pair for the Lane's integration /
  auto-resolution session, so the Lane runs entirely on the resolved pair. A serial Iteration
  feeds its own single session the same way; a pair is never resolved and then dropped.
- **Resolve once.** Resolve **once** per issue at pickup; the Orchestrator MUST NOT switch model
  or effort mid-session.
- **Escalate a stall once (contract 1.22).** An Orchestrator that routes MUST retry an issue whose
  session ended in **silent no-progress** at a single configured **escalation rung**, resolved at
  that issue's *next* pickup and reported with source `escalated`. The rung is one `(model,
  effort)` pair, a **config-file-only** tier like `[routing]` itself, on by default; an explicit
  `--model` / `--reasoning-effort` (flag or env) suppresses escalation exactly as it suppresses
  routing, because a deliberate pin means what it says. Four properties are load-bearing and MUST
  hold: escalation is triggered by **silent no-progress alone** (a timeout answered with a slower,
  higher-reasoning pair near-guarantees a second timeout; a crash is evidence about the harness;
  an explicit no-more-tasks is the Agent stating there is nothing to do); it is **once** — one
  rung, never a ladder — and **sticky** for the rest of the Run, so the issue never falls back to
  the pair that already stalled on it; it is a **no-op** where the routed pair already equals the
  rung, and MUST then keep the source it routed with rather than claim a change that did not
  happen; and it MUST tick no **Strike**, because the mechanism that aborts a stuck Run must not
  punish trying harder. It is a property of the *issue*, not of the mode that stalled it: an
  Orchestrator with more than one pickup seam MUST feed and read one ledger from all of them.
  A mid-session switch is still forbidden — escalation is a **second pickup**, which is why it is
  stated here and not in the bullet above.
- **Give an issue a bounded number of attempts (contract 1.26).** An Orchestrator MUST hold one
  monotonic per-issue, per-**Run** **Attempt lifecycle** — `fresh` → `retrying` → `skipped`, the
  states [`attempt-lifecycle.json`](../git-loopy/conformance/attempt-lifecycle.json) declares —
  and MUST dispose of each of the five **Session outcomes** as that fixture's table states: a
  silent no-progress and a crash move the issue one step, and a timeout, an explicit
  no-more-tasks and a content-filtered turn move it straight to `skipped`. An Iteration that
  advanced its issue reached no ending and MUST move it neither forward nor back. The lifecycle
  MUST NOT regress — including on an advancing Iteration between two failures, because an issue
  that landed something once under a Run that cannot finish it is the ordinary shape of a Run
  grinding rather than evidence the Run recovered.
  It is **per Run and in memory**: an Orchestrator MUST NOT write it to the tracker, because a
  demotion there would outlive the Run that made it and would put the runner inside the triage
  state machine it is only ever a consumer of (§2).
  Skipping is a **pickup filter, never a Pool filter**: the Pool MUST stay whole — the Pool-wide
  closure whitelist (§5), the collection Event (§12) and the emptiness test (§6) are all computed
  over every eligible issue — and only the candidate list narrows. Where a pickup seam declines a
  candidate it had already chosen, that decline MUST leave a `wrapper.pickup.skipped` record (§12)
  rather than the candidate vanishing from consideration. Where a seam's only decline returns the
  candidate to the very list it was chosen from — a released reservation, which is re-offered on
  the next turn — the lifecycle MUST instead narrow that list, because a decline re-taken every
  turn would emit one skip record per turn forever; the record the Run owes is the one the seam
  that defeated the issue already left. Like the rung above, it is a property of the *issue*: an
  Orchestrator with more than one pickup seam MUST feed and read one lifecycle from all of them,
  and every one of those seams MUST honour it. It MUST NOT be carried by repurposing a mode-local
  scheduling guard — a concurrency or re-work guard is a different question with a different
  lifetime, and a lifecycle defeat written into one is indistinguishable from a collision
  afterwards. The lifecycle is what a resolution's **lifecycle position** reports (`fresh` is
  `fresh`; everything past it is `retrying`), which is how a same-pair crash retry reads as a
  retry at all.
  **The lifecycle is what the Strike counts (contract 1.27).** An Orchestrator with a Pickup MUST
  charge exactly one **Strike** per issue that reaches `skipped`, at the ending that puts it
  there, and MUST charge nothing for an unproductive Iteration or an unpublished contribution
  (§6) — an issue the Run is still willing to retry has not been given up on, and an Iteration is
  not a thing the Run can give up on at all. Because the lifecycle is a property of the issue and
  not of the seam that observed it, the Strike MUST be charged where the ending is observed, which
  is the one seam every pickup mode already shares. And an Iteration whose Pickup finds the Pool
  non-empty but can bind none of it MUST end the Run under the `all_skipped` reason (§10) rather
  than record anything: with no-progress no longer charging the ceiling, that Iteration spends no
  session and charges nothing, so it would otherwise re-walk the same Pool and skip the same
  candidates until the Iteration cap.
- **Publish what it resolved (contract 1.21).** An Orchestrator that resolves a Routing resolution
  MUST carry it on that Pickup's own `wrapper.pickup.bound` (§12) — the pair, the tier, the raw
  keys, the gate warnings, the source and the lifecycle position — and MUST NOT mint a second
  Event for it. The resolution happens *at* the Pickup, for the Pickup's key, so a second record
  would be one instant described twice with the same cardinality. The fields are
  **optional-when-present**: a port that resolves nothing (see the Python-only note below) says
  nothing and stays conforming, and a consumer MUST treat their absence as "this Runner does not
  route" rather than as a route it failed to report. A `null` `model` or `effort` is *present*
  and means the backend chooses — for `effort`, the accompanying gate warning is what separates
  an operator who asked for nothing from one whose effort was dropped.
- **Read back what it parsed (contract 1.24).** An Orchestrator that routes MUST print, at **Run
  start** and **unconditionally**, a labelled readback of the model settings it parsed: the
  run-wide **Default pair**'s model, effort and context tier; the **Escalation rung**; whether an
  explicit pin suppressed routing run-wide; every `[routing]` entry; and the CLI version it
  spawns beside the CLI version used for its roster's latest observed-capability refresh
  ([ADR-0019](adr/0019-roster-derived-from-the-pinned-harness.md)). Three properties are
  load-bearing. It MUST echo the routing **keys themselves**, never a count of them: no validator
  for the table can exist — its keys are the operator's vocabulary and its pairs are the vendor's
  — so the operator reading back what the kit parsed is the only validation available anywhere,
  and a count can reveal neither a half-filled table nor a key spelled differently from the label
  it was meant to match. Under an unselected Route policy it MUST gate-check
  **each configured pair**, non-fatally, because a route
  no issue exercises is otherwise never resolved and its model id and effort never checked at all,
  and because a rung is otherwise first gated at a stalled issue's *next* pickup — the one moment
  the Run is already going badly. And the block MUST print on a Run that configured **nothing**,
  because the Run that puzzles an operator is the one where nothing they wrote appeared to take
  effect, and a block that appeared only when something was configured would be silent exactly
  there. The readback decides nothing and MUST NOT stop a Run: a readback that could would be the
  validator that cannot exist. An Orchestrator that publishes it on the wire carries it on that
  Run's own `wrapper.run.start` (§12), on the same optional-when-present terms as the Pickup's
  resolution above.
- **Declare that it routes (contract 1.25).** Every Orchestrator MUST declare
  `insight_capabilities.routing` on `wrapper.run.start` (§12) — `true` when it resolves a Routing
  resolution per Pickup, `false` when it resolves none. The two records above are
  optional-when-present precisely so a port that does not route stays conforming by staying
  silent, and silence is the one thing a **Dashboard** cannot interpret on its own: an empty Route
  column is *this Runner never routes* under one port and *no Pickup has happened yet* under
  another. The declaration is what separates them, and it is per-distribution — a Run that pins
  `--model` still publishes a resolution, sourced `defaulted_explicit_override`, so suppressing
  routing MUST NOT flip this key to `false`.

Contract 1.12 gave the serial loop a structural pickup seam of its own (§3.3), and contract 1.20
applies what that seam resolves. In between, the scope deliberately did **not** move with it: a
serial Iteration resolved its Routed pair at Pickup, refused an unknown `task-type:` key there
and skipped the candidate (§3.3), but then ran on the run-wide pair — the pair it had just
resolved was discarded. That reservation is now **withdrawn**
([ADR-0037](adr/0037-routing-takes-effect-in-every-mode.md), reversing
[ADR-0027](adr/0027-routing-is-calibrated-by-measurement.md)'s *"Calibration only affects Parallel
mode"*): **the pair a Pickup resolves is the pair its session runs on, at every parallelism a Run
can have.** `git-loopy` with no flags is serial, so the old scope made the *default* invocation
the one invocation where a configured `[routing]` table changed nothing, and an Orchestrator MUST
NOT reintroduce it.

This decision is pinned by three language-neutral fixtures in the
[Conformance suite](../git-loopy/conformance/README.md):
[`model-roster.json`](../git-loopy/conformance/model-roster.json) (the canonical
`model → accepted efforts` sets — its keys are the supported-model set — beside `context_tiers`,
the tier half of the same roster),
[`routing-resolution.json`](../git-loopy/conformance/routing-resolution.json) (labels + config →
the **Routing resolution** record and whether it warns, and the closed `routing_sources`
vocabulary every Runner reads instead of minting its own names), and
[`effort-gate.json`](../git-loopy/conformance/effort-gate.json) (model + requested effort → gated
result and whether it warns). The Python reference adapter drives all three against the production
`resolve_iteration_model` and `gate_reasoning_effort` seams and asserts its in-language roster
constant equals `model-roster.json`.
[`attempt-lifecycle.json`](../git-loopy/conformance/attempt-lifecycle.json) is the fourth, and
pins the two dials one ending turns together — the **Attempt lifecycle**'s states and per-ending
disposition beside whether the same ending owes the **Escalation rung** — because the two are
separate ledgers reading one record, and a fixture that pinned either alone would let them drift.

**Routing is Python-only today**, and that is a *recorded decision* rather than an omission: the
shell and PowerShell Orchestrators implement no part of this section, so neither resolves a Routed
pair and neither has one to apply or to discard. A port that routes nothing is conforming, not
behind, and MUST NOT be held to §14 until it reaches Config parity (§11, phase 3). What such a
port MUST NOT do is arrive carrying the **Parallel-only scope** contract 1.20 withdrew: a native
port that implements routing acquires the rule above with it, and applies the resolved pair to a
serial Iteration exactly as to a Lane. Cross-language routing is deferred, not discharged; this
paragraph is the deferral.

`model-roster.json` MUST carry a **`cli_version`** stamp naming the Copilot CLI used to capture
its refreshed, observed capability rows. Reasoning-effort capability is not vendor data:
`models.list` discards CAPI's
advertised array and substitutes a table hardcoded in the CLI bundle, so the roster is a function
of **CLI version** ([ADR-0019](adr/0019-roster-derived-from-the-pinned-harness.md)) and an
unstamped roster cannot distinguish a correction from a defect. The stamp is a statement about the
fixture, not about the harness an Orchestrator spawns: where the two differ the divergence is
reportable, and reconciling them is a pinned-harness bump plus a regeneration, made as one change.

The SDK 1.0.14 migration records one explicit compatibility exception in ADR-0019:
seven preexisting effort rows absent from the upgrade account's listing keep their previous
values. They are identified in the Runner README and are not claims of verification against
the stamped CLI or of account availability. The exception does not admit new, unobserved
effort sets: Gemini 3.8 stays on the unknown-model warning-and-pass-through path. The stamp
therefore identifies the observed refresh, not the provenance of those retained rows.

The same stamp governs `context_tiers`, the **context tier** capability
([ADR-0017](adr/0017-context-tier-and-live-context-gauge.md)) that shares the roster rather than
forming a parallel table, so the family has one lockstep point with the live catalog instead of
two. A model with **no** `context_tiers` row is unknown and its tier passes through untouched,
exactly as an off-roster model keeps its effort; a row is added only when that model's tiers were
captured for the stated `cli_version`, in the same regeneration ADR-0019 requires.

### 14.1 The measured tier

The family precedence spine (§11) gains one rung between the operator's Config and the built-in
default, so a routing table git-loopy authored can supply a **Routed pair** — but **only where the
operator is silent**:

CLI flag > env var > project Config > global Config > **measured** > built-in default

- **A hand-written entry always wins.** A `[routing]` key in either Config scope beats the measured
  entry for the same **Task type**, with no override flag and no special case, because that is the
  chain that already shipped. Measured entries fill the Task types Config leaves alone, beside the
  ones it does not.
- **An explicit override suppresses it with the rest of routing.** `--model` /
  `--reasoning-effort` (flag or env) already suppresses routing run-wide; the measured tier is
  routing, so it is suppressed too. An operator who names a pair gets that pair, and a
  **Calibration** cannot quietly reintroduce a different one.
- **Deleting the artifact is how an operator opts out.** Routing falls straight back to Config and
  the built-in defaults, with nothing else to undo.

The tier reads **one artifact**, `git-loopy/routing.measured.toml`, in the project scope dir beside
`config.toml` and in the same TOML dialect. It is:

- **Committed**, so a change arrives as a diff a human can read, question and revert (ADR-0028).
- **Current state only.** Git is the ledger: `git log -p` is every past Calibration in order,
  `git blame` names which one set a Task type's model, `git revert` undoes a bad one.
- **Machine-written and never hand-edited.** There is deliberately no measured Config scope for
  `config set` to write to, and no free-text key anywhere in the file for something to write an
  opinion into.

Each Task type's record carries one of four states, and only two of them supply a Routed pair:

| Status | Supplies a pair | What it says |
| --- | --- | --- |
| `measured` | yes | A completed Calibration with a winning pair. The only state that is *evidence*. |
| `incomplete` | no | A search that hit a ceiling or was interrupted. Carries where it stopped and **no pair at all** — a stopped search publishes no winner. |
| `demoted` | no | A pair removed after it stopped making progress on real work. The pair is cleared; which pair failed, and after how many, is kept. |
| `provisional` | yes | A pair **in force that was never measured** (ADR-0030) — what **Demotion** installs when it steps up the price staircase into a rung nobody trialled. Carries no evidence, and MUST NOT be reported as measured. It records the pair it replaced, a closed-vocabulary `reason`, and `replaced_after_no_progress` — the count of that pair's no-progress contributions that met the threshold, required because **Demotion** is the only thing that writes this state and a record of a failure that names no count is not evidence of one. |

An Orchestrator MUST NOT treat an unrecognised status as either of the two that route: a row it
cannot classify supplies nothing and falls through to the built-in default.

**The measured tier is Python-only today**, on the same terms as routing itself: the shell and
PowerShell Orchestrators implement no per-issue routing, and therefore no measured tier. They
declare it unsupported here rather than by implication — a port that reads no artifact is
conforming, not behind — and MUST NOT be held to reading, writing or reporting one. Cross-language
measured routing is deferred, not discharged; this paragraph is the deferral, and it moves when a
port reaches Config parity (§11, phase 3).

The tier is pinned by two more language-neutral fixtures:
[`routing-resolution.json`](../git-loopy/conformance/routing-resolution.json)'s
`precedence_cases` (measured + both Config scopes + an explicit override → resolved pair and the
**tier** that supplied it, including a `provisional` entry that routes while reporting itself
unmeasured), and
[`calibration-search.json`](../git-loopy/conformance/calibration-search.json) (the cheapest-first
price staircase a **Calibration** walks, and where each ceiling stops it).

### 14.2 A `task-type:` label's origin is unobservable

Routing reads a label and never infers a Task type from content **at routing time** — §14's first
rule is unchanged. What changed is who may have written the label: the **Task-type classifier**
(ADR-0029) infers a Task type from an unlabelled issue's own content, once, before routing, and
writes it back to the tracker.

An Orchestrator MUST NOT depend on a label's origin. A human-set and a classifier-written
`task-type:` label are the same string on the same issue, the tracker records no difference, and
routing MUST resolve both identically.

Classifying is optional; classifying *carelessly* is not. An Orchestrator that runs the
classifier MUST satisfy all four of the following (contract 1.23):

- **On a named pair, never the run-wide default.** The classifier's own `(model, effort)` MUST
  come from a source an operator can point at — its own Config key, else the cheapest rung of a
  measured price staircase (§14.1). Borrowing the run-wide default would let one unmeasured prior
  decide every issue's **Task type**, and so every **Routed pair**, while appearing nowhere as a
  routing input. Where neither source yields a pair, the Orchestrator MUST NOT classify at all:
  an issue routes as it would have without the classifier, which is a known state, whereas a
  hardcoded fallback is a guess made in exactly the condition where nobody can check it.
- **Nothing spent on an issue that is already labelled.** Inference is a one-off that ends when
  the label exists, not a toll every Run pays to re-derive what the tracker already says.
- **The closed taxonomy at the write.** A proposal outside §14's seven keys MUST be refused
  rather than written, because attaching a `task-type:` label to an issue creates that label in
  the tracker: an invented key is not a bad routing decision that expires with the Run, it is a
  permanent addition to the vocabulary §14 closed.
- **No classification failure may cost the Iteration or tick a Strike.** Every way of not
  producing a Task type — a refused proposal, an unreadable answer, a failed session, a rejected
  tracker write — MUST leave the issue on the **Default pair** and leave the **Iteration**
  exactly as it would have been. Failing real work over a label inverts the priority, and a
  classifier that could strike out would let an unattended Run end without ever having attempted
  the work.

Where the Orchestrator classifies, it does so at the **Pickup** — after the issue is bound and
before the Routing resolution is resolved — and it MUST route on what it inferred even where the
tracker refused the write. The write is what saves the *next* Run the inference; losing it must
not also lose the decision it recorded.

### 14.3 The Static route (contract 2.8)

Everything above describes the routing an Orchestrator does when no **Route policy** was selected.
An operator MAY select one. The policy is a single Config key, `route_policy`, resolved on the
family precedence spine (§11) like any other. Three values are in the vocabulary: `unselected` —
the default, and the absence of a decision — `static` (this section), and `dynamic` (§14.4).

- **Selected, never inferred.** An Orchestrator MUST NOT read the absence of a policy as a choice
  of one, and MUST NOT reinterpret an existing Config as though `static` had always been in force.
  Subject to the Python-local migration guard below, a Run that names no policy keeps every rule
  in §14 exactly, gate warnings and all. An
  Orchestrator MUST refuse a policy name it does not implement rather than falling back to
  `unselected`: a name it silently ignored would run the Run under a policy the operator did not
  ask for and believes is active.
- **A route is an atomic triple.** Under `static` the **Routing resolution** that binds an
  Iteration is a complete `(model, reasoning effort, context tier)`. An existing `[routing]` entry
  or run-wide default is a valid Static route and inherits the run-level context tier; an
  Orchestrator MUST NOT assemble a route out of per-Task-type fragments of some other policy.
- **The authenticated harness is the authority.** An Orchestrator MUST verify a Static route
  against the model listing of the **authenticated harness this Run actually spawns** — its
  eligibility for this account, its reasoning-effort dial, and the context tiers it offers. It MUST
  NOT verify against the hardcoded model roster (§14's gate), a published plan or licence
  catalogue, or another CLI installation, and MUST NOT satisfy the read from a listing memoised
  earlier in the Run: the **Rate card**'s listing is memoised precisely so it cannot reprice
  mid-Run (ADR-0026), which is the opposite of the freshness this check needs. Reading capabilities
  MUST NOT rewrite billing provenance the Run has already recorded.
- **A remote placement's harness is another installation.** Where an **Execution host** (§20) opens
  its work sessions on a machine that authenticates as *itself*, the orchestrator's own listing
  describes a different installation under a different identity, and reporting it as that
  placement's verdict is exactly the substitution the rule above forbids. An Orchestrator MUST
  refuse the combination before work rather than verify the wrong harness. Only a placement whose
  sessions run under the Run's own authenticated harness is verifiable today.
  Python's no-I/O placement refusal precedes local model listing, Skill migration, interactive
  detachment and remote host preparation, including the green-base preflight dispatch. It shares
  the routing authority verdict used by setup, doctor and Run preflight; a run-wide model/effort
  override does not waive it. The `execution_host_refusal` cases in `routing-resolution.json`
  exercise recorded and temporary authority through CLI, interactive startup and direct Run,
  preserving Config and starting no work, Lease, Strike or Route publication. An explicitly
  unselected remote Run retains its staged legacy path, not strict routing support. This
  refusal does not complete non-local activation or change shell/PowerShell's routing deferral.
- **Refuse, never rescue.** §14's *gate and fall back* rule does not apply to a Static route and
  MUST NOT be reached for: an effort the model does not accept, a tier it does not offer, a model
  this account may not use, a model the harness never listed, and a listing that could not be read
  at all each end the Run under `preflight_failed` (exit `1`) **before any work**, naming the
  setting and where it was configured. Dropping an effort, downgrading a tier, or substituting a
  backend default is not a successful route: it is a different route than the one selected,
  reported as success.
- **No effort dial is not the effort `none`.** Where the harness states a model has no
  reasoning-effort dial, an Orchestrator MUST send **no effort argument at all** for it. That is a
  distinct fact from a model whose dial offers the *value* `none`, which MUST be sent as a value.
- **Fixed for the Agent, and across retries.** A Static route MUST stay fixed for an Iteration's
  Agent and for every permitted retry of that issue. §14's **escalation rung** still applies where
  the operator explicitly configured one, and the rung is verified like any other route; a rung an
  Orchestrator ships **by default** is not explicit authorization and MUST NOT promote a Static
  route. Attempt and **Strike** accounting is unchanged either way.
- **One resolution, everywhere.** The verified triple is the same **Routing resolution** that
  configures the session, rides `wrapper.pickup.bound`, and reaches the CLI and the **Dashboard**.
  An Orchestrator MUST NOT re-derive it, and MUST NOT publish a gated readback beside an ungated
  session. `wrapper.run.start`'s readback MAY carry `route_policy`; a consumer MUST NOT read an
  empty `gate_warnings` under `static` or `dynamic` as roster approval — it means the roster was
  not asked. This includes retained Static routes and explicit escalation under Dynamic policy.

The policy is pinned by [`routing-resolution.json`](../git-loopy/conformance/routing-resolution.json)'s
`static_route_cases` (one harness listing plus one route → `accepted` or a closed-vocabulary
refusal, with the no-dial / effort-`none` and unreadable / empty distinctions exercised explicitly)
and its `static_route_notes`.

**The Static route is Python-only today**, on the same terms as routing itself and for the same
reason: the shell and PowerShell Orchestrators implement no per-issue routing and read no harness
model listing, so they have no route to verify. They declare it unsupported in
[`fixture-claims.json`](../git-loopy/conformance/fixture-claims.json) rather than by implication.
The Dashboard needs no policy-aware branch — it renders the verified triple off
`wrapper.pickup.bound` exactly as it renders any other.

**Staged Python-local migration guard (contract 2.9, #567).** A local Python Run with nonempty project or
global Config MUST supply or inherit an explicit `static`/`dynamic` Route policy before Agent
work. Absence remains absence, not implicit Static consent: the Runner refuses with an actionable
`update --routing keep`/`migrate` or temporary `--route-policy`/`GIT_LOOPY_ROUTE_POLICY` remedy,
without prompting or rewriting Config. A model/effort override alone is not this decision.
CLI startup checks before Skill migration, listing or detachment, rechecks after a Config reload,
and carries saved-Config presence through detached startup. Doctor and Run preflight use the
same authority verdict; live readiness and Pickup validation still apply after authority exists.
Historical records retain their interpretation. Empty Config scopes and unselected non-local
Runs retain the legacy path during staged activation; a selected policy still MUST NOT validate
a remote placement using local eligibility. Shell and PowerShell migration enforcement is
explicitly deferred and their unchanged behavior remains conforming. This paragraph is the
member deferral, not final Dynamic-default activation, remote capability support, or Subagent/
Integration routing support.

`routing-resolution.json`'s `migration_recovery` exercises this guard through the real
CLI-to-Run-to-work-session seam, in serial and Lane modes. Its cases MUST first refuse
unselected saved Config unchanged, then supply temporary or recorded authority. The adapter
MUST compare actual work-session settings, canonical Pickup and Dashboard readback, not merely
the configuration resolver's answer. Retained Static rows and their inherited tier remain
authoritative under either choice; a run-wide model/effort pin requires neither leaderboard
access nor a selector, while a context-only override still uses the strongest selector.
Migration readiness is not future Pickup authority: loss of required access or evidence MUST
refuse uncovered Dynamic work without a fallback session, final publication or Strike, while
eligible Static work remains usable. Runs MUST leave saved Config unchanged; temporary
authority MUST expire on the next invocation. These executable cases do not claim the
remaining activation obligations or change the default policy.

The sibling `first_setup` matrix starts with **neither Config scope present** and
drives explicit `init --routing` into the same serial/Lane work seam, for project
and global setup. Dynamic setup MUST collect finite bounds and authored associations
without seeding Static rows or storing the operator-owned key. Missing access,
invalid bounds, no verified candidates or unavailable required evidence/capabilities
MUST refuse before saving operator choices or writing tracker labels. Repairing the
input and retrying setup MUST allow a later, independently validated Run to use the
recorded policy without temporary overrides. Keep MUST need neither leaderboard
access nor Dynamic limits. The actual session, canonical Pickup, Dashboard and
final tracker comment MUST agree, and the Run MUST preserve both Config scopes.
This is an executable obligation for Python's explicit opt-in setup, not a change
to bare init or auto-setup. Shell/PowerShell first-setup activation is deferred;
historical streams and the non-local, Subagent and Integration boundaries above
are unchanged.

### 14.4 The Dynamic route (contract 2.8)

Under `dynamic` the route for one issue is **elected from live public benchmark evidence** rather
than written down in advance (ADR-0057). It is opt-in, and the rules below are what make the
election an answer an operator can audit rather than a plausible-looking guess.

Contract 2.9 includes §14.3's staged migration guard and opt-in first setup,
the affected-work refusal and shared preflight-deadline obligations below,
Route publication (§14.5) and Routing preparation (§14.6). The affected
`routing-resolution.json`, `event-schema.json` and `dashboard-insights.json`
fixtures declare that provenance at 2.9; Event wire compatibility remains 1.2.
This declaration correction adds no Event fields, activates no Dynamic defaults,
and leaves fixture cases and historical streams' interpretation unchanged.

- **Opt-in, with its own prerequisites, or no dynamic work at all.** The policy requires the
  operator's own authorized access to the evidence source, a finite assessment deadline, a per-Run
  routing-credit allowance, a bounded selector concurrency, and the verified associations between
  benchmark identities and harness configurations. Incomplete Dynamic prerequisites MUST refuse
  affected Dynamic work, never an otherwise authorized and freshly validated Static route.
  Missing Task types may still be classified to discover Static applicability without leaderboard
  access, but only within explicit valid routing limits and the same Run Consumption ledger.
  Missing, invalid or exhausted limits MUST admit neither a classifier nor a Route selector.
  Uncovered work MUST NOT reach a fallback work session or buy Bump-class classification after
  its Dynamic refusal. If nothing can advance, the Run MUST stop nonzero with the actual reason,
  not claim an empty Pool or spend an attempt or Strike on work that never started. Setup and
  doctor report that same incomplete readiness as a failure; setup MUST NOT save an unready
  Dynamic choice. Authority and Static validation refusals still stop the Run before work.
  §14.3's remote-placement rule applies unchanged
  and for the same reason: an **Execution host** that authenticates as itself is another
  installation, and a route verified against this machine's listing is not a verdict about that
  one.
- **The credential is the operator's, and the repository stays here.** The access key MUST be read
  from the environment only: never embedded in the distribution, never written into Config, never
  serialized into a detached child's payload, and never echoed into diagnostics or Events. An
  Orchestrator MUST NOT send repository content, issue prose, or any other local material to the
  evidence source, which is asked for published measurements and nothing else.
- **Evidence travels with its provenance and its unknowns.** An admitted record MUST keep its
  source identity, the time it was retrieved, and whatever measurement date, benchmark version and
  conditions the source published. A value the source did not publish is an explicit unknown — a
  null — and MUST NOT be rendered as zero or dropped. A missing score is not a score of zero, and a
  similar name is not proof of identity: an association an operator has not verified MUST be
  excluded with a reason rather than inferred from spelling.
- **Elect deterministically, from the verified intersection.** The **Route selector** is the
  highest-Intelligence-Index configuration that is both verified and runnable on the authenticated
  harness, at its matched effort, in the smallest supported context tier that fits the bounded
  input. An exact score tie breaks on comparable published speed and then on stable identity. An
  Orchestrator MUST NOT select the selector with the selector, and MUST NOT downgrade it to a
  cheaper configuration to stay inside a limit — a limit is a refusal, not a discount.
- **A Static route still wins, and the Task type is settled first.** §14.3's routes — a `[routing]`
  entry, an explicit flag or environment pin, a configured **Escalation rung** — are instructions,
  and an Orchestrator MUST NOT spend a selector call to contradict one. A missing **Task type** is
  classified *before* applicability is resolved, and an existing classification is respected.
- **A context-only Run override constrains work, not the selector.** An explicit context-tier
  flag or environment override MUST fix the work tier without suppressing Dynamic model/effort
  selection. Work candidates MUST support that exact tier on freshly read capabilities; an empty
  set MUST refuse assessment and work rather than downgrade the tier. The strongest Route
  selector and its smallest input-fitting tier remain independently elected, even if that model
  cannot run work at the requested tier. The override MUST survive detached startup and take part
  in relevant-input identity for proposal validation and cross-Run reuse. Persisted run-level
  context remains the inherited tier for Static pairs, not a context-only Run override.
- **The assessment is read-only and bounded.** It receives the issue, its acceptance criteria, the
  settled Task type, bounded relevant repository context, and admitted local measurements. It MUST
  NOT implement the work, run Trials, or audit the whole repository, and untrusted issue prose MUST
  NOT be able to escape its tool, policy, candidate or output boundaries: an answer naming anything
  other than one of the candidates it was handed is invalid, not a route.
- **Forecast is not measurement.** The proposal's summary MUST distinguish what was forecast from
  what was measured. Published inference speed MUST NOT be described as a measured duration for
  this issue under this harness, and a reliability estimate the evidence does not support MUST NOT
  be invented.
- **Freshly validated at Pickup, and never mid-Agent.** Evidence and eligibility are checked at
  preparation *and* again at the **Pickup** that binds the issue. Unchanged verified inputs need
  not buy a second selector call; a cached response MUST NOT be presented as fresh, and a candidate
  that changed or was invalidated MUST NOT start under its old route. A proposal takes no **Lease**,
  and a bound route does not change under a running Agent.
- **Provenance lands before the work does.** The elected route MUST be recorded locally — which
  evidence elected it, retrieved when, assessed by which selector, at what cost — **before** the
  work session opens, and a recording that fails refuses the route. The resulting triple is the
  same **Routing resolution** §14.3 describes: it configures the session, rides
  `wrapper.pickup.bound` under the `dynamic` source, and reaches the CLI and the **Dashboard**
  unchanged.
- **Routing spends Consumption, and exhaustion is final.** Classification and selector attempts and
  their retries count toward routing usage and the Run's **Consumption**. An Orchestrator MUST
  enforce the deadline and the admission allowance, bound selector concurrency, disclose billing
  overshoot already in flight, and admit no further routing calls once either bound is exhausted.
  The routing deadline starts before live routing preflight, including a listing shared with
  retained Static validation; completing that validation MUST NOT start or reset the clock.
  Deadline exhaustion blocks assessment, not eligible already-classified Static work.
  Python's **Task-type classifier** and **Route selector** `usage.tokens` records
  are **Run**-only (`iter: null`, no **Lane contribution**), not **Consumption**
  of the work that happens to be open. Their billing remains visible in Run
  totals/readback even when no work is admitted. Historical records without an explicit scope retain their
  existing interpretation; no new Event field is needed.
  An observed bill consumes admission allowance while its assessment remains open,
  not only when the assessment finishes. Completion or cancellation MUST retain
  that charge exactly once, including any additional bill reported during
  cancellation cleanup. A result returned after the deadline MUST NOT become a
  Task-type label or Routing proposal; its observed Consumption remains recorded.
- **Refuse, never fall back.** A required-source failure, quota exhaustion, an empty verified
  intersection, invalid selector output, unavailable eligibility, or a failed local recording each
  yield an explicit *unavailable* decision. An Orchestrator MUST NOT substitute stale evidence, the
  run-wide default, or a cheaper selector, and MUST preserve authorized Static routes and
  already-running work. A candidate refused this way is passed over for the Run rather than
  retried in place: re-admitting it immediately would spend the whole allowance on one issue.
- **A permitted later attempt reassesses; it does not inherit a rung.** Where the **Attempt
  lifecycle** already admits another attempt on an issue, its new **Pickup** MUST elect again from
  current evidence and eligibility, supplied together with what the issue's earlier attempts ran on
  and how they ended. An Orchestrator MUST NOT reserve a configuration for a later attempt — the
  first dynamic election may already take the strongest one available — and MUST NOT substitute
  §14's fixed escalation rung, which under this policy is not a route anybody elected. Reassessment
  creates no attempt: it MUST NOT reset or bypass a per-issue attempt or **Strike** limit, and a
  later route that cannot be elected blocks the affected work under the rule above rather than
  becoming an attempt that never ran.
  An explicitly configured Static escalation rung still wins, even when it equals the
  run-wide default that a dynamic election would otherwise replace.
- **An infrastructure failure is not evidence about a configuration.** A crash, a policy-refused
  turn, an exhausted wait and an explicit no-more-tasks each say something other than *this
  configuration could not do this work*, and an Orchestrator MUST NOT read them as capability
  evidence. Exactly one ending is: the session that ran to the end, claimed no failure, and left
  nothing behind. Even that MUST NOT remove a configuration from consideration — every eligible
  configuration stays a candidate at every attempt, and what re-electing one costs is a stated
  justification rather than a veto. Repeating a configuration without one is invalid output, not a
  route.
- **Where the issue is and what it will run on are separate answers.** The **Routing resolution**,
  the work session's own settings, and the CLI and **Dashboard** history MUST agree on the elected
  configuration, and MUST report the attempt's **lifecycle position** beside it rather than in place
  of it. A reassessed retry that re-elects the same configuration is otherwise indistinguishable
  from a first election, and the position MUST NOT be derived from how many earlier attempts there
  were: an **Iteration** that advanced its issue reaches no ending and spends no attempt, yet is a
  real earlier attempt the next election is told about.
  Bounded history MUST retain earlier capability failures before recent advances. Omitted
  advances still count toward the recorded session ordinal and the relevant input identity;
  truncation MUST NOT reset numbering or make changed attempt history reusable.

- **A later Run may revalidate a decision, and may never replay one.** An Orchestrator MAY reuse a
  routing result its own earlier Run recorded instead of electing again, but only after it has read
  the live sources fresh and found every relevant input — the issue and its context, the policy, the
  model's current capability and eligibility, the evidence, and the attempt history — unchanged. The
  reusable view MUST be *derived* from the Orchestrator's own canonical event history; it MUST NOT be
  a second authoritative store, a committed table, or anything read back from a tracker comment or a
  **Route label**, which §14.5 already forbids as a routing input. A reused result MUST NOT authorize
  work or take a **Lease** on its own, MUST NOT carry a model past a current capability or policy
  check, MUST NOT manufacture an attempt or rewrite billing provenance, and MUST still yield to a
  Static or run-wide route. Where anything relevant moved, the Orchestrator elects again inside the
  same routing limits, or refuses the work under the rule above — a route it could not revalidate is
  never a route it may assume.
- **A revalidation is recorded, and says what it reused.** Reuse MUST write its own
  `wrapper.routing.resolved` record naming the original decision, the selector settings and the
  evidence provenance it re-checked, so a CLI or **Dashboard** readback can tell freshly validated
  reuse from a new assessment and from a recorded route that stopped validating — from the canonical
  records rather than from a recomputed explanation. Nothing routing itself writes — that record, its
  timestamps, a published Route comment, an owned Route label — may reach the compared inputs. An
  Orchestrator whose own output invalidates its next comparison reassesses every Run and has
  implemented no reuse at all.

The policy's vocabulary is pinned by
[`routing-resolution.json`](../git-loopy/conformance/routing-resolution.json) (`static_route_policies`,
the `dynamic` **Routing source**, the case in which an elected route outranks every label-derived
one, and `dynamic_retry_cases`, which states totally what each ending tells the *next* election) and
its provenance record by
[`event-schema.json`](../git-loopy/conformance/event-schema.json)'s `wrapper.routing.resolved`
contract and the rolling stream that carries one.

The same routing fixture's `retry_lifecycle` matrix exercises these existing
rules after Python's explicit saved migration, through the real CLI into serial
work and a Lane followed by a serial retry. It observes actual session settings,
canonical Pickup, outcome history, CLI/Dashboard readback and idempotent tracker
publication rather than inferring execution from a resolver result. Selector
bills cross the SDK session transport into Run-only Consumption, including an
invalid retry that starts no work. Each CLI Pickup line is observed separately
from startup and earlier Pickups. Its cases
cover changed and repeated elections, required repeat justification, infrastructure
failure, advancing work, attempt/allowance exhaustion and explicit Static
escalation. A refused retry spends no task attempt or Strike, while later
eligible Static work still runs. Saved Config remains unchanged. The existing
one-Lane-per-issue rule is preserved; this matrix does not grant a second Lane,
activate final defaults or extend routing to another Runner member or placement.

The `in_flight_consumption` matrix carries recorded init/update authorization
through the real unattended CLI in serial and local Lane modes. Its eight cases,
each at exact allowance exhaustion and with overshoot, keep an assessment open
while the next candidate is refused. They observe configured concurrency,
the unchanged strongest selector, completion/cancellation/late-result settlement,
actual frozen work settings, canonical records, separate CLI Pickup lines,
Dashboard Run-only Consumption and final tracker publication. Pending candidates
remain open without a final assignment or Route projection; no unstarted work
charges a Strike and Config remains unchanged. These are shared obligations for
the existing opt-in Python flow, not final-default or native-member activation.

**The Dynamic route is Python-only today**, for the same reason §14.3 is: the shell and PowerShell
Orchestrators implement no per-issue routing and read no harness listing, so they have no route to
elect. They declare it unsupported in
[`fixture-claims.json`](../git-loopy/conformance/fixture-claims.json) rather than by implication.
The Dashboard needs no policy-aware branch — it renders the elected triple off
`wrapper.pickup.bound` exactly as it renders any other.

The **Run readback** MUST distinguish an absent Static table from retained Static
routes. Under unsuppressed `dynamic`, `unconfigured_task_type_keys` names work awaiting
Dynamic Pickup, not a promise to use the Default pair. A fully covered table MUST
NOT be described as having uncovered Task types. An absent fixed Escalation rung
does not disable permitted outcome-aware Dynamic retries. A run-wide model/effort
override still suppresses Dynamic work, and historical unselected-policy records
retain their existing meaning. These clarifications change no Event fields or
compatibility-schema version.

**Dynamic routing is off by default and stays off until an operator selects it.** An Orchestrator
MUST NOT enable it by inference from the presence of a key, an association table, or any other
prerequisite.

**Reuse is local to the clone that recorded it.** The canonical history it derives from is the
Orchestrator's own Run logs, so reuse never crosses a machine, a checkout, or an operator — two
clones of one repository each elect once and then each revalidate their own decision. This is a
boundary rather than a gap: a shared reusable store would be the second route authority §14.5
exists to prevent, and an event history is a record of what *this* Orchestrator did. A Run whose
history is absent, pruned or unreadable therefore elects afresh and says so; that is the ordinary
case every Run before reuse existed was already in, and it MUST NOT refuse a **Pickup**.

### 14.5 Route publication (contract 2.9)

The final **Routing resolution** remains local and authoritative. The tracker is
an output projection, never a routing input: a tracker comment or Route label
MUST NOT pin, select, validate, invalidate, or otherwise alter a later
resolution.

- **Record before projecting or working.** The final `wrapper.pickup.bound`
  record MUST persist before an Agent session or any tracker publication starts.
  Failed local recording starts no work. Tracker delivery is non-blocking once
  that record exists: a permission failure, rate limit, transient failure, or
  partial delivery MUST be retained as pending/failed local delivery state and
  MUST NOT be reported as published.
- **Project finals only.** Every materially changed final static or Dynamic
  assignment gets one idempotent append-only comment with an identity, its exact
  model/effort/context-tier values, an issue-safe source rationale, and
  provenance references. A proposal and unchanged revalidation get no comment.
  The projection MUST omit credentials, raw prompts, private repository
  excerpts, and hidden reasoning.
- **Own one association, not a repository label.** A projection MAY attach one
  deterministic compact Route label that encodes the selected triple and is
  collision-resistant within tracker limits. Exact values remain in the local
  record and comment. Rerouting MUST replace only that issue's owned Route-label
  association, preserving Task-type and unrelated labels; it MUST NOT rename a
  shared repository label.
- **Do not read your own output back.** A Runner that renders an issue for an
  **Agent** or for a **Route selector** MUST exclude its own Route projection
  from that rendering — both the owned Route label and the projection comment,
  and the comment before any "most recent N comments" window is taken. A
  projection left in is a tracker write that changes the assessment's relevant
  input, which is the invalidation loop the first rule of this section forbids,
  and it spends a comment slot reserved for what a human or an earlier
  iteration actually said.
- **Retry without time travel.** Pending delivery MUST survive restart and retry
  within a finite bound using the comment identity. A retry must not duplicate a
  comment already accepted by the tracker, and an obsolete delivery MUST NOT
  overwrite a newer Route label. Startup retries and subsequent Pickups of the
  same assignment MUST share that bound: an exhausted assignment remains visibly
  failed without further tracker I/O, even after restart or restored access.
  A materially changed final assignment has its own finite retry bound; publication
  exhaustion MUST NOT prevent its delivery or block locally recorded work.
  A failed replacement can leave the previous owned association in place, or no
  association if removal succeeded before the add failed. Neither is a current
  Routing resolution: local delivery MUST remain failed, not published, and the
  tracker projection MUST NOT authorize work or unbounded repair calls.
  Delivery state is published separately from the Routing resolution so the CLI
  and Dashboard distinguish an undecided Route or
  failed Agent from an already-decided Route whose tracker projection is pending
  or failed.

The `publication_recovery` matrix in `routing-resolution.json` exercises recorded
Python-local init/update authorization through repeated real CLI Runs and actual
serial/Lane sessions. It pins fresh reuse and its original provenance, permission,
rate-limit and transient failures, idempotent partial recovery, exhausted delivery
across Pickups (including a previous or missing owned association), and a changed assignment
after capability withdrawal. Actual work
settings, canonical Pickup and Dashboard route readback must agree while Config
and unrelated labels remain unchanged. This is a Python activation obligation;
shell/PowerShell implementation remains deferred, and historical streams,
Subagent and Integration settings are unchanged.

### 14.6 Routing preparation (contract 2.9)

A Runner MAY prepare **Routing proposals** for candidates it has already
established as eligible, ahead of the **Pickups** that would bind them. A
prepared proposal is nonbinding: it is an input to a Pickup's own fresh
validation and never a substitute for one.

- **Preparation is not dispatch.** A proposal MUST NOT reorder the Pool, reserve
  or lease a candidate, alter a running Agent, or bypass dependency and attempt
  admission. It is not evidence the Pool is empty, and a candidate that only
  ever gets prepared MUST remain exactly as pending as it was.
- **The Pickup stays authoritative.** The session's final model, reasoning
  effort and context tier MUST come from the Pickup, which re-reads both live
  sources and compares the relevant input identity before it binds anything.
  Unchanged verified inputs MUST NOT rerun the **Route selector**; changed
  issue content, capabilities, evidence or policy MUST invalidate the proposal
  and buy another selection only within the remaining bounds. A proposal past
  its validity window MUST NOT be bound — the Pickup assesses again instead.
  Bounding the selector's prompt MUST NOT hide a relevant source change:
  the input identity also covers the full normalized issue, parsed runnable
  Feedback-loop commands, and admitted local measurement behind that prompt.
  Unrelated repository prose is not a routing input.
- **Prioritise the next Pickup, and spend nothing on the ineligible.** The next
  candidate to be worked is prepared first. Finishing an Iteration MUST NOT
  wait for unrelated preparation. A Pickup MAY interrupt that preparation to
  free routing capacity, but MUST NOT cancel another authoritative Pickup's
  claimed assessment. Blocked, unreadable or otherwise
  ineligible candidates remain visibly pending and MUST cost no classifier or
  selector call for preparation. A missing **Task type** is classified at
  preparation *before* static applicability is checked; existing labels stay
  authoritative and a **Static route** MUST avoid the selector entirely.
  The candidate MUST be re-read when its bounded preparation actually starts,
  not merely when the pass was scheduled. Task-type classification and selector
  calls share admission limits; reading an existing label is not a classification
  attempt. Persisting the settled Task type MUST NOT itself invalidate a proposal.
  When serial-required work is discovered while Lanes drain, it takes preparation
  priority over speculative Lane candidates without starting its work early.
- **Only while a Run is running, and only within the operator's bounds.**
  Preparation MUST run on the Run's own event loop under the configured
  selector concurrency and routing-credit allowance, and MUST stop for the rest
  of the Run once either is spent. Discovering an issue while no Run is active
  MUST NOT start a background routing service, and no preparation loop may
  become unbounded or speculative.
  Cancellation MUST leave an explicit `unavailable` preparation outcome and
  MUST be joined before the Run closes its local Event log. An interrupted
  assessment is not a reusable proposal. Already-routed Agents and eligible
  Static work remain usable even when new Dynamic work is refused.
- **Concurrent checks MAY share one in-flight read.** Two preparations asking
  the same live source the same question at the same instant MAY join a single
  request, and a provider-supported unchanged response (for example an
  `ETag`/`304` revalidation) MAY validate the same snapshot — without reporting
  that the benchmark was rerun. Neither is a cache: a read that has already
  finished MUST NOT be replayed to a later caller.
- **Publish state as a proposal.** Preparation outcomes are recorded as
  `wrapper.routing.prepared` with a `state` of `proposed`, `static`, `reusable`
  or `unavailable`. CLI and Dashboard projections MUST present them as
  proposals — never as a final binding, an acquired Lease, or an empty Pool —
  and MUST NOT let a proposal populate the route a Pickup is responsible for.
  The proposal's summary, selector settings, evidence identity, retrieval times,
  and available measurement metadata accompany its record. Missing measurement
  dates, versions, and conditions remain unknown; retrieval is not measurement.
  Readers accept historical preparation records lacking this additional provenance.
- **Preparation is clone-local and Run-scoped.** Proposals are held in memory
  for the life of the Run and are never shared between clones or Runs; a
  cross-Run saving is the **Reusable route** of §14.5's sibling rule, not this
  one.

The shared `routing-resolution.json` `pool_revalidation` matrix composes these
rules through recorded migration and the real local Python CLI in serial and
Lane modes. It holds already-bound Agents open until another eligible issue has
a proposal, then changes issue text, evidence, eligibility or Readiness, or
withdraws required evidence, before the next Pickup. Unchanged inputs reuse the
proposal; changed relevant inputs require reassessment. Actual session settings,
canonical Pickup, CLI/Dashboard readback, SDK-observed Run-only Consumption and
final tracker publication must agree. Initially Blocked and unreadable candidates
buy no classification or selection. Preparation takes no Lease or final
publication; existing Pickup-time Lease acquisition and release remain unchanged.
Running Agents finish on their frozen settings, while a refused Dynamic candidate
leaves retained Static work usable without a Strike for unstarted work.
Rolling may filter newly ineligible candidates on its fresh Pool read before
reservation, rather than inventing a Pickup skip. This is Python-local composed
Conformance, not final Dynamic-default activation. Shell/PowerShell activation is
deferred; no non-local, Subagent or Integration routing is claimed.

The companion `pool_priority` matrix carries recorded migration through four
eligible pending candidates. It preserves oldest-first order and explicit
**Priority**, prepares the next candidate before selectors assess other candidates,
and exercises selector concurrency of one and two with more eligible
candidates than available slots in either case. Running Agents remain open
until those other assessments are in flight; the next actual serial Pickup or
Lane refill advances without waiting for those unrelated assessments.
Interrupted selectors retain their SDK-observed Run-only Consumption and an
explicit unavailable preparation record before Run end. Pending issues keep
their labels and receive no Lease, final route or tracker publication. Actual
settings, canonical Pickup, separate CLI Pickup lines, Dashboard and final
publication agree, without Config edits or changes to the existing ordering
and Lease rules. These are additional Python-local obligations at the same
staged activation seam, not a change to the member or placement deferrals above.

## 15. Release and compatibility identity (MUST)

The **Release version** is product identity, not a compatibility shortcut. `--version` and
`wrapper.run.start` MUST report the same exact Release version for one distribution. No other
Event is required to repeat it, and advancing the Wrapper contract does not advance the Event
schema or record format.

Components selected as artifacts of one packaged distribution MUST have exact Release-version
equality and fail closed on drift. An externally discovered TUI helper from another Release MAY
remain usable when Event-schema and capability negotiation prove compatibility, but the
Orchestrator MUST warn that the Release versions differ. Release equality alone MUST NOT establish
cross-release compatibility.

## 16. Release-line advancement (MUST)

Every **Orchestrator** MUST advance the **Release line** for a closed issue
with a Bump class other than `semver:none`, after its Integration has published
the issue and while holding the `_integration_lock` that serializes Integration
([ADR-0009](adr/0009-runner-driven-integration-and-auto-resolution.md)). The
advance derives its Release target by ratcheting the closed Bump-class labels
and increments that target's `dev.N` counter; it MUST NOT be performed in a
Lane contribution. The resulting Release-line commit is therefore a
post-Integration fact, not work a Lane proposes.

After a successful Release-line commit, the Orchestrator MUST emit
`wrapper.release.advanced` with the closed `issue`, its `bump_class`, the
ratcheted `release_target`, and the committed `release_version`. A
`semver:none` issue and a failed advance emit no such Event. A closed
`vX.Y.Z` milestone may **Promote** the current development line to stable, but
does not select the target; `semver:major` is deliberately exempt from that
milestone trigger and may Promote unattended. [ADR-0052](adr/0052-the-release-line-advances-per-issue.md)
records both the ratchet and that unattended-major consequence as deliberate.

What happens when a human closes a milestone-bearing issue outside a **Run** is
open: this contract does not say whether that closure advances the Release
line. See ADR-0052's [Still open](adr/0052-the-release-line-advances-per-issue.md#still-open)
section.

## 17. Closed-world Skill policy (Skill-policy rollout, MUST)

A Run's capability set is a **contract**, not an accident of the operator's machine. Every
Orchestrator MUST resolve exactly which canonical Skill names a Run may load, freeze that answer
before the first Iteration, and record it. See
[ADR-0015](adr/0015-closed-world-skill-policy.md) for the decision. The language-neutral cases are
pinned by [`skill-policy.json`](../git-loopy/conformance/skill-policy.json).

### 17.1 Vocabulary

| Term | Meaning |
| --- | --- |
| **Skill catalog** | The inventory of Skills an operator may inspect and select, with one **winner** per canonical name carrying a `source_kind`. Discovery reads metadata only; catalog membership never makes a Skill available to a Run. |
| **Skill policy** | The git-loopy-owned closed-world set of names one scope persists or supplies, e.g. `enabled_skills`. |
| **Skill baseline** | The initial enabled/disabled selection copied once from the external agent client when the first policy is established. It seeds a policy and is never a live authority. |
| **Effective Skill policy** | The single immutable resolution of every policy source for one Run: enabled names, Required Skills, legacy denials, resolved source kinds, base scope, and fallback reason. |
| **Minimal Skill policy** | Exactly the **Required Skills** and nothing else. The answer whenever no base policy is in effect. |
| **Required Skill** | A name the active Run instructions declare in their `required-skills` metadata. A Run whose effective set omits one is invalid. |

A Skill is identified by **canonical name** — never by absolute path or content digest — so a
project policy stays portable. Canonical names match `[a-z][a-z0-9]*(-[a-z0-9]+)*`. The
`source_kind` vocabulary is exactly `project`, `inherited`, `personal`, `plugin`, `custom`,
`builtin`, and `packaged`. Source precedence when resolving a catalog winner is: the
Orchestrator's **installed catalog** — the pinned external Skill catalog it installs into its own
config home at setup and refreshes at the start of every Run, reported as `packaged` — then the
Copilot CLI's own project/personal/plugin/built-in/custom precedence. The consuming repository's
`<repo>/.copilot/skills` is **not** an Orchestrator Skill source, so no winner an Orchestrator
resolves carries `source_kind` `project`; the value remains in the vocabulary for older Event
streams (ADR-0025). Enabling a plugin-provided Skill MUST NOT activate the rest of its owning
plugin.

### 17.2 Source precedence and scope replacement

The base policy is selected from **one** scope, never merged across scopes:

1. **project** — the project Config's `enabled_skills`, when the key is present.
2. **global** — the global Config's `enabled_skills`, when no project key is present.
3. **minimal** — the Minimal Skill policy when neither key is present.

A present-but-empty list is a real empty policy, **not** inheritance: absence and explicit empty
MUST remain distinguishable all the way from Config parsing to the resolver.

`GIT_LOOPY_ENABLED_SKILLS` is an **exact replacement** of the selected base policy for one Run
(including an explicit empty value). Replacement changes the *names*, not the *selection*:
`base_scope` and `fallback` describe which scope the base came from, so an environment
replacement over a project policy still reports `project`, and an environment replacement with no
configured scope at all still reports `minimal`. §17.6's startup classification — not
`base_scope` — is what answers "was this installation ever configured".

The repeatable `--enable-skill` and `--disable-skill` flags are temporary Run **overlays** applied
after replacement: enable adds, disable subtracts, and **disable wins** over both the base policy
and a same-Run enable.

`deny_skills`, `GIT_LOOPY_DENY_SKILLS`, and `--deny-skill` are **deprecated final guards**. They
may only subtract from the effective set, are applied last, are reported verbatim even when they
name nothing enabled, and MUST NOT be silently dropped or weakened. A legacy denial that would
remove a Required Skill is a validation failure, not a quiet subtraction.

### 17.3 Validation failures (preflight, MUST)

Resolution MUST fail before any work begins, and MUST NOT rewrite persisted policy, when:

| Failure | Condition |
| --- | --- |
| Inventory unavailable | The catalog could not be resolved and the policy was explicitly configured. |
| Missing enabled Skills | An enabled name has no catalog winner. |
| Missing Required Skills | A Required Skill is not in the effective enabled set. |
| Untracked project Skills | An enabled winner whose `source_kind` is `project` is not git-tracked. Unreachable since ADR-0025, which removed the project Skill source; retained so an Orchestrator that still exposes one keeps failing closed. |

Each failure MUST name the offending canonical names, sorted and deduplicated. A Run with **no**
explicit policy still resolves the Minimal Skill policy even when external inventory is
unavailable, because Required Skills come from the installed catalog.

### 17.4 Freeze semantics (MUST)

The Effective Skill policy is resolved **once** at Run preflight, before source collection and
before any agent session exists, and is frozen for the entire Run: every Iteration and every
parallel **Lane** shares that one immutable boundary. Later catalog changes, Copilot CLI state
changes, or Config edits MUST NOT alter a Run in flight. Disabled Skills are omitted from the
session-visible catalog *and* denied again at the permission gate.

### 17.5 `wrapper.skill_policy.resolved` (MUST when the policy surface is implemented)

One Run-scoped Event (`iter: null`) records the frozen boundary, with exactly these payload keys:

| Key | Value |
| --- | --- |
| `base_scope` | `project`, `global`, or `minimal`. |
| `enabled` | Sorted deduplicated canonical names. |
| `fallback` | `minimal`, `migration`, or `null` when a base scope was in effect. |
| `legacy_denied` | Sorted deprecated denial names. |
| `migration_warning` | `true` when the active prompt declared no `required-skills` and inherited the packaged list. |
| `required` | Sorted Required Skill names. |
| `source_kinds` | Enabled name → resolved `source_kind`. |

Every collection projection is sorted, so two Runs with the same boundary produce byte-identical
payloads. The Event is **redacted**: it carries canonical names only. Absolute paths, home
directories, the Run-scoped exposure directory, and Skill content MUST NOT appear. Serialization
follows §12 — envelope keys first, payload keys sorted.

### 17.6 Startup state and the Python-first native transition

Before resolution an Orchestrator MUST classify what the Run found: `unconfigured` (no Config
resolves anywhere), `legacy` (Config exists but the selected scope predates `enabled_skills`), or
`configured` (a base policy is in effect from a scope or an environment replacement). A Run
overlay alone does **not** make a legacy base configured — it is temporary and persists nothing.

Skill policy is a family requirement, but the Python reference Orchestrator implements it first.
Until a port reaches Config parity it MUST **fail closed** rather than silently ignore a
configured policy: detecting `GIT_LOOPY_ENABLED_SKILLS` (including an explicit empty value),
`--enable-skill`, `--disable-skill`, or an `enabled_skills` key in a standard Config location MUST
abort before source collection and before the agent is invoked, naming the unsupported surface.
Legacy deny-only invocations continue to resolve and run unchanged. Silently proceeding with a
wider capability set than the operator configured is the one outcome this section exists to
prevent.

### 17.7 Consulted Skills are a different fact

`skill-consultation.json` measures which Skills an Iteration actually *used*; this section governs
which Skills a Run *may* use. A consulted name is per-Iteration observed behaviour, a policy name
is Run-level availability, and neither may be derived from the other.

## 18. Changing this contract

1. Update this document and bump the **Contract version**.
2. Add or update the corresponding **Conformance** fixture(s).
3. Update **every** Orchestrator (Python + each port) to pass the new fixtures.
4. If `PROMPT.md`'s commit-message convention changed, update `CLOSE_KEYWORD_RE`
   (`git-loopy/python/git_loopy/wrapper.py`) and the shell/PowerShell equivalents together.

No Orchestrator lands a contract change alone — the Conformance suite fails any port left behind,
which is the whole point of the backbone. §13.1 narrows how that can fail: a fixture no port has
been asked about cannot land at all, and a port that has been asked and has not implemented the
answer is recorded as *owing* the fixture, with a tracking issue, instead of passing green for never
having heard of it. The suite still cannot compel a port to implement anything. What it refuses is
the silent case.

---

**See also:** [`docs/runners.md`](runners.md) (the operator-facing runner reference),
[ADR-0013](adr/0013-multi-language-runner-family.md) (the family decision),
[`CONTEXT.md`](../CONTEXT.md) (the glossary).
