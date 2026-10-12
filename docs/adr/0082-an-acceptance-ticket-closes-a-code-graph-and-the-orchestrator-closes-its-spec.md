# An acceptance ticket closes a code graph, and the Orchestrator closes its Spec

**Status:** accepted design; implementation acceptance is tracked by the delivery issues.

Decided by [Decide how a task graph closes for code
work](https://github.com/bradcstevens/git-loopy/issues/658).

In Hermes' Kanban orchestrator (no goal) pattern, a synthesis card waits on every
worker and produces the deliverable, and the root card wakes when all its children
complete ([evidence](../research/wanderloots-hermes-coordination/hermes-ui-reference.md)).
git-loopy had no counterpart for code. Each issue's **Review verdict** judges that
issue's head against that issue (ADR-0072), and **Integration** keeps base green, but
no step judges a Spec's outcome as a whole, and nothing closes the Spec when its work
is done. On 2026-10-12, five of the seven open `Spec:` and `PRD:` issues had every
sub-issue closed. Of the 19 closed ones with sub-issues, 16 were closed in four batch
sweeps, a median of 3.5 days after their last child closed and up to 43 days after;
the latest sweep, a planning-document audit, closed eight at once.

git-loopy 1.0.0 closes a code graph in two steps. An **Acceptance ticket** verifies
the integrated result against the Spec, and a **Spec closure** by the
**Orchestrator** then closes the Spec.

## The acceptance ticket

A Spec carries numbered User Stories and Testing Decisions, not acceptance criteria;
only tickets carry `## Acceptance criteria`. Acceptance therefore judges the
integrated base against the Spec's User Stories.

- **Shape.** The **Planner** adds one executable ticket to every Spec-derived code
  graph of two or more tickets. It is typed `test`, its acceptance criteria are the
  Spec's User Stories, and every other ticket in the graph blocks it through a native
  dependency (ADR-0047), so it waits in Todo until their work is integrated. To the
  Runner it is an ordinary issue: **Pickup**, the `test` **Agent profile**
  (ADR-0078), the **Review stage** and Orchestrator-owned closure apply unchanged. No
  marker, Event or Spec awareness is added for it.
- **Deliverable.** One acceptance test per automatable User Story, run on the
  integrated base, and a closing story map that names, for every story, the test or
  commit that delivers it. The result is a head, so the **Reviewer** gates it like any
  other result. A comment-only acceptance would leave the Review stage nothing to
  judge.
- **One-ticket graphs** get no acceptance ticket. The single ticket's acceptance
  criteria must cover every User Story, so its Review verdict is the acceptance.
- **Revisions.** A graph revision wires each new executable ticket into the open
  acceptance ticket, or adds a fresh one when the previous acceptance ticket has
  closed. Graph approval (ADR-0071) checks that the acceptance ticket exists and that
  every executable ticket blocks it.
- **No Spec, no acceptance.** An issue filed without a Spec has no User Stories; its
  Review verdict is its whole closure. A graph with no code ticket gets no acceptance
  ticket either.

`/to-tickets`' "final integrate-and-verify ticket" for a wide refactor is a different
job: it is where a shared branch turns green, so it blocks the acceptance ticket like
any other ticket.

## A gap blocks the acceptance ticket

A User Story that no closed ticket delivered is a gap. The acceptance Agent:

1. lands acceptance tests only for the stories that pass, so base never goes red;
2. files each gap as one Follow-up issue (ADR-0076) labelled `needs-triage`, never
   `ready-for-agent`, carrying the story and its failing test as the reproduction;
3. records each Follow-up as a native blocker of its own ticket, posts the story map
   with the gaps, and ends without closing it.

The acceptance ticket goes back to Todo. The Planner adopts the Follow-ups in a graph
revision, and the new dependency edge already requires the whole-graph review of
ADR-0071, so gap work becomes executable only through human approval. When the
Follow-ups close, the acceptance ticket is Ready and runs again.

The acceptance Agent never reopens a closed ticket, which would undo an approved,
Orchestrator-owned closure and make the work Pickup-admissible again without a human.
It never fixes a gap itself, which has no size bound and skips approval. A report with
no blocker is not enough: an open `ready-for-agent` issue with no open blocker is
picked up again by every Run.

**The authority comes from the ticket.** The Run instructions let an Agent file a
`needs-triage` Follow-up and record it as its own blocker only when its approved issue
body asks for that. The acceptance-ticket template in `/to-tickets` does, as a paired
Skill change (ADR-0064). Every other Agent keeps today's rule: a blocker is recorded
only on an issue that already exists.

## Spec closure

When one of the Orchestrator's own closures leaves no sub-issue of a `Spec:` or `PRD:`
planning document open, the Orchestrator closes that document as completed, with a
comment linking the closure that completed it. The rule is structural. It reads the
closed issue's parent and that parent's open sub-issues, and needs no marker: the
acceptance ticket closes last by construction, and a one-ticket graph completes on its
only ticket.

- A human's closure, for any reason, never triggers it. A Spec whose last child a
  human closed as not planned stays for a human.
- It never closes a `wayfinder:map`, which closes by its own rule.
- It applies to Specs from before 1.0.0, which have no acceptance ticket.
  `/to-tickets`' cleanup rule, "close the Spec once every child has closed", becomes
  the Orchestrator's.
- It is best effort. The Orchestrator verifies the closure as it verifies an issue
  closure, and records the Spec closure, or its failure, as a Run Event. A failure is
  reported and left to the **Loop engineer**. Nothing sweeps for Spec closures later,
  so a Spec already open with every child closed stays with a human.

Agents still never modify a parent Spec, and the Run still never picks up a planning
document. This closure is the Orchestrator's only write to one.

## Scope

The GitHub Issues source on the Python path (ADR-0073). The legacy local-markdown
source carries no dependencies and its `prd.md` has no state (ADR-0080), so it has
neither an acceptance ticket nor a Spec closure. The dormant shell, PowerShell and
Rust paths take this on only if they are resumed. Release notes stay with the Release
line (`docs/releases/README.md`): a graph is not a release.

## Considered options

- **No whole-outcome step.** Rejected: per-issue Review and Integration cannot see a
  story no ticket covered or behaviour that exists only across tickets, and Spec
  closure has been bookkeeping done in batches, days after the work.
- **A whole-Spec review the Orchestrator starts.** Rejected: the Reviewer is
  head-bound and read-only (ADR-0072, ADR-0078). This review would have no head,
  nothing to remediate and no card on the board, and its trigger would need the Runner
  to read Specs.
- **Acceptance by the Loop engineer outside the Run.** Rejected: that is today's
  practice, and the record shows it happening in batches, days after the work.
- **A story map without tests, typed `review`.** Rejected: with no head it would
  bypass the Review stage or need a review mode with no head.
- **A new `acceptance` Task type.** Rejected: the taxonomy is closed (ADR-0029) and
  each key is an Agent profile (ADR-0078).
- **The Loop engineer closing the Spec.** Rejected: once the last child closes, the
  close is deterministic work, and leaving it to a person is the drift the record
  shows.
- **A marker that identifies the acceptance ticket.** Rejected: the structural rule
  needs none, and a one-ticket graph would need the marker as well.
- **A Preflight sweep** that closes every Spec whose sub-issues are all closed.
  Rejected: it would close a Spec whose last child a human closed as not planned.
