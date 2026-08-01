---
name: wayfinder
description: Plan a huge chunk of work — more than one agent session can hold — as a shared map of decision tickets on your issue tracker, and resolve them one at a time until the way to the destination is clear.
disable-model-invocation: true
---

A loose idea has arrived — too big for one agent session, and wrapped in fog: the way from here to the **destination** isn't visible yet. Wayfinding is about finding that way, not charging at the destination. This skill charts the way as a **shared map** on the repo's issue tracker, then works its **decision tickets** — questions whose resolution is a decision, not slices of a build to execute — one at a time until the route is clear.

The destination varies per effort, and naming it is the first act of charting — it shapes every ticket. It might be a spec to hand off and iterate on, a decision to lock before planning starts, or a change made in place like a data-structure migration. The map is domain-agnostic — engineering work, course content, whatever fits the shape.

## Plan, don't do

Wayfinder is **planning** by default: each ticket resolves a decision, and the map is done when the way is clear — nothing left to decide before someone goes and does the thing. The pull to just do the work is usually the signal you've reached the edge of the map and it's time to hand off. An effort can override this in its **Notes** — carrying execution into the map itself — but absent that, produce decisions, not deliverables.

## Refer by name

Every map and ticket is an issue, so it has a **name** — its title. In everything the human reads — narration, the map's Decisions-so-far — refer to it by that name, never by a bare id, number, or slug. A wall of `#42, #43, #44` is illegible; names read at a glance. The id and URL don't vanish — a name wraps its link — but they ride _inside_ the name, never stand in for it.

## The Map

The map is a single issue on this repo's issue tracker, labelled `wayfinder:map` — the canonical artifact. Its tickets are child issues of the map.

The map is an **index**, not a store. It lists the decisions made and points at the tickets that hold their detail; a decision lives in exactly one place — its ticket — so the map never restates it, only gists it and links.

**Where the map, its child tickets, blocking, and frontier queries physically live is tracker-specific.** The issue tracker should have been provided to you — run `/setup-agent-skills` if not. Consult the tracker doc's "Wayfinding operations" section for how _this_ repo expresses them. If no tracker has been provided, default to the local-markdown tracker.

### The map body

The whole map at low resolution, loaded once per session. Open tickets are **not** listed — they are open child issues, found by query.

```markdown
## Destination

<what reaching the end of this map looks like — the spec, decision, or change this effort is finding its way to. One or two lines; every session orients to it before choosing a ticket.>

## Notes

<domain; skills every session should consult; standing preferences for this effort>

## Decisions so far

<!-- the index — one line per closed ticket: enough to judge relevance, then zoom the link for the detail the ticket holds -->

- [<closed ticket title>](link) — <one-line gist of the answer>

## Not yet specified

<!-- see "Fog of war": in-scope fog you can't ticket yet; graduates as the frontier advances -->

## Out of scope

<!-- see "Out of scope": work ruled beyond the destination; closed, never graduates -->
```

### Tickets

Each ticket is a **child issue** of the map; the tracker's issue id is its identity. Its body is the question, sized to one 100K token agent session:

```markdown
## Question

<the decision or investigation this ticket resolves>
```

Each ticket carries a `wayfinder:<type>` label — one of `research`, `prototype`, `grilling`, `task` (see [Ticket Types](#ticket-types)).

A session **claims** a ticket by assigning it to the dev driving the map, **first**, before any work, so concurrent sessions skip it. That assignee _is_ the claim: an open, unassigned ticket is unclaimed.

Blocking uses the tracker's **native** dependency relationship — essential because it renders the frontier _visually_ in the tracker's own UI, so the human sees what's takeable without opening the map. Only a tracker that lacks native blocking falls back to a body convention. A ticket is **unblocked** when every ticket blocking it is closed; the **frontier** is the open, unblocked, unclaimed children — the edge of the known.

The answer isn't part of the body — it's recorded on resolution (see [Work through the map](#work-through-the-map)). Assets created while resolving a ticket are linked from the issue, not pasted in.

## Ticket Types

Every ticket is either **HITL** — human in the loop, worked _with_ a human who speaks for themselves — or **AFK**, driven by the agent alone. A HITL ticket only resolves through that live exchange; the agent never stands in for the human's side of it (a grilling agent that answers its own questions has broken this).

- **Research** (AFK): Reading documentation, third-party APIs, or local resources like knowledge bases to surface a fact a decision waits on. Resolved by a `/research` **subagent**. Use when knowledge outside the current working directory is required.
- **Prototype** (HITL): Raise the fidelity of the discussion by making a cheap, rough, concrete artifact to react to — an outline, a rough take, a stub, or UI/logic code via the /prototype skill. Links the prototype as an asset. Use when "how should it look" or "how should it behave" is the key question.
- **Grilling** (HITL): Conversation. The default case. Always invoke the /grilling and /domain-modeling skills.
- **Task** (HITL or AFK): Manual work that must happen before a _decision_ can be made — nothing to decide, prototype, or research, but the discussion is blocked until it's done. Signing up for a service so its API can be judged, provisioning access, moving data so its shape can be seen. This is the one type that _does_ rather than decides — and it earns its place by unblocking a decision, not by delivering the destination. The agent drives it alone where it can (AFK); otherwise it hands the human a precise checklist (HITL). Resolved when the work is done; the answer records what was done and any resulting facts (credentials location, new URLs, row counts) later tickets depend on.

## Fog of war

The map is _deliberately_ incomplete: don't chart what you can't yet see. Beyond the live tickets lies the **fog of war** — the dim view of decisions and investigations you can tell are coming but can't yet pin down, because they hang on questions still open. Resolving a ticket clears the fog ahead of it, graduating whatever's now specifiable into fresh tickets — one at a time, until the way to the destination is clear and no tickets remain.

The map's **Not yet specified** section is where that dim view is written down: the suspected question, the area to revisit later. It's the undiscovered frontier _toward_ the destination — everything here is in scope, just not sharp enough to ticket. Write as loosely or as fully as the view allows; it doubles as a signpost for collaborators reading where the effort is headed.

**Fog or ticket?** The test is whether you can state the question precisely now — _not_ whether you can answer it now.

- **Ticket when** the question is already sharp — even if it's blocked and you can't act on it yet.
- **Not yet specified when** you can't yet phrase it that sharply. Don't pre-slice the fog into ticket-sized pieces: it's coarser than a ticket, and one patch may graduate into several tickets, or none, once the frontier reaches it.

**Not yet specified** excludes what's already decided (Decisions so far), what's already a live ticket, and what's out of scope (the next section).

## Out of scope

Fog only ever gathers _toward_ the destination. The destination fixes the scope, so work beyond it is **out of scope** — it isn't fog, and it doesn't belong in **Not yet specified**. It gets its own **Out of scope** section on the map: work you've consciously ruled out of _this_ effort. Scope, not sharpness, lands it here.

Out-of-scope work never graduates — the frontier stops at the destination — so it returns only if the destination is redrawn, and then as a fresh effort, not a resumption.

Ruling something out of scope is a scoping act, not a step on the route. When a ticket that already exists turns out to sit past the destination — mis-scoped in while charting, or exposed by a resolution — **close it** (a closed ticket is unambiguously off the frontier) and leave one line in the **Out of scope** section: the gist plus why it's out of scope, linking the closed ticket. It stays out of **Decisions so far**, which records the route actually walked — a scope boundary isn't a step on it.

## Invocation

Two modes. Either way, **never resolve more than one ticket per session** — with the exception of research tickets.

### Chart the map

User invokes with a loose idea.

1. **Name the destination.** Run a `/grilling` and `/domain-modeling` session to pin down what this map is finding its way to — the spec, decision, or change. The destination fixes the scope, so it's settled first.
2. **Map the frontier.** Grill again, **breadth-first** this time: fan out across the whole space rather than deep on any one thread, surfacing the open decisions and the first steps takeable now. **If this surfaces no fog** — the way to the destination is already clear, the whole journey small enough for one session — you don't need a map. Stop and ask the user how they'd like to proceed.
3. **Create the map** (label `wayfinder:map`): Destination and Notes filled in, Decisions-so-far empty, the fog sketched into **Not yet specified**.
4. **Create the tickets you can specify now** as child issues of the map — then wire blocking edges in a **second pass** (issues need ids before they can reference each other). Wiring sorts them into the frontier and the blocked; everything you can't yet specify stays in the fog — the **Not yet specified** section.
5. **Fire the research subagents.** For each `research` ticket you just created, spin up a `/research` subagent to resolve it in parallel, capturing its findings on a throwaway `research/<name>` branch with a context pointer from the ticket.
6. **Publish the charting transition** — see [Publish the wayfinding transition](#publish-the-wayfinding-transition).
7. Stop — charting is one session's work; it hand-resolves nothing.

### Work through the map

User invokes with a map (URL or number). A ticket is **optional** — without one, you pick the next decision, not the user.

1. Load the **map** — the low-res view, not every ticket body.
2. Choose the ticket. If the user named one, use it. Otherwise take the first frontier ticket in order. **Claim it**: assign it to yourself before any work.
3. Resolve it — **zoom as needed**: fetch the full body of any related or closed ticket on demand; invoke the skills the `## Notes` block names. If in doubt, use `/grilling` and `/domain-modeling`.
4. Record the resolution: post the answer as a **resolution comment**, **close** the issue, and **append a context pointer** to the map's Decisions-so-far.
5. Add newly-surfaced tickets (create-then-wire); graduate any fog the answer has made specifiable, clearing each graduated patch from **Not yet specified** so it lives only as its new ticket. If the answer reveals a ticket — this one or another — sits beyond the destination, **rule it out of scope** rather than resolving it on the route. If the decision invalidates other parts of the map, update or delete those tickets.

The user may run unblocked tickets in parallel, so expect other sessions to be editing the tracker concurrently.

## Publish the wayfinding transition

Charting a map and resolving a ticket are both durable workflow transitions, and this skill owns
them. Record each one so the next session — human or agent — reads the same answer you did
instead of reconstructing it from labels, blocking edges and comments.

```bash
git-loopy continuation publish --input /tmp/publish-wayfinding.json
```

**Only publish after the transition is durable.** For charting that means the map issue, every
ticket you specified, and every blocking edge already exist on the tracker; for a resolution it
means the resolution comment is posted and the ticket is closed. Then post one short
transition-evidence comment on the map saying what this session changed and what it came from,
and capture its comment id from the URL `gh issue comment` printed. Never write the Continuation
record, its `<!-- git-loopy-continuation... -->` marker, or its index label yourself — the command
owns the carrier comment, and a hand-written one is not a record.

`<producer-login>` is the authenticated tracker account (`gh api user --jq .login`).

### Charting

One record, carried on the map, with one Action per ticket you created plus — **only if
`## Not yet specified` is non-empty** — exactly one `Chart workstream` Action for the remaining
in-scope fog. Fog is coarser than a ticket, so it gets one Action, not one per suspected question:
guessing the shape of what you cannot yet phrase is the thing the fog section exists to avoid.

The template below carries one Action of each `wayfinder:<type>` shape. Keep the shapes for the
ticket types you actually created, repeat a shape once per ticket of that type, and drop the rest.
Repeat the `dependency-satisfied` prerequisite once per ticket blocking that ticket, and drop it
where nothing blocks.

<!-- continuation-request: chart-map -->
```json
{
  "repository": "<repository>",
  "trusted_producers": ["<producer-login>"],
  "completion": {
    "continuation_contract_version": "1.2",
    "record_format": 1,
    "publication": "shared",
    "disposition": "continue",
    "workstream": {
      "anchor": {
        "kind": "issue",
        "repository": "<repository>",
        "number": "<map-issue>"
      },
      "destination": {
        "kind": "sub-issues-complete",
        "target": {
          "kind": "issue",
          "repository": "<repository>",
          "number": "<map-issue>"
        }
      }
    },
    "transition": {
      "owner": "wayfinding",
      "evidence": [
        {
          "kind": "issue-comment",
          "repository": "<repository>",
          "issue": "<map-issue>",
          "comment_id": "<evidence-comment>"
        }
      ]
    },
    "producer": {"login": "<producer-login>", "role": "planning"},
    "carrier": {
      "kind": "issue",
      "repository": "<repository>",
      "number": "<map-issue>"
    },
    "actions": [
      {
        "key": "grilling-<grilling-ticket>",
        "summary": "Resolve the decision on ticket <grilling-ticket>",
        "kind": "Resolve decision",
        "occurrence": "v1",
        "instruction": {"mode": "skill", "value": "/wayfinder <map-issue> <grilling-ticket>"},
        "target": {
          "kind": "issue",
          "repository": "<repository>",
          "number": "<grilling-ticket>"
        },
        "basis": [
          {"kind": "issue", "repository": "<repository>", "number": "<map-issue>"}
        ],
        "prerequisites": [],
        "interaction": {
          "classification": "HITL-required",
          "evidence": {
            "kind": "human-boundary",
            "reason": "human-decision",
            "resolution_condition": {
              "kind": "issue-closed",
              "target": {
                "kind": "issue",
                "repository": "<repository>",
                "number": "<grilling-ticket>"
              }
            }
          }
        },
        "completion_condition": {
          "kind": "issue-closed",
          "target": {
            "kind": "issue",
            "repository": "<repository>",
            "number": "<grilling-ticket>"
          }
        }
      },
      {
        "key": "research-<research-ticket>",
        "summary": "Surface the fact ticket <research-ticket> waits on",
        "kind": "Research fact",
        "occurrence": "v1",
        "instruction": {"mode": "skill", "value": "/research <research-ticket>"},
        "target": {
          "kind": "issue",
          "repository": "<repository>",
          "number": "<research-ticket>"
        },
        "basis": [
          {"kind": "issue", "repository": "<repository>", "number": "<map-issue>"}
        ],
        "prerequisites": [],
        "interaction": {
          "classification": "AFK-safe",
          "evidence": {
            "kind": "transition-owner-attestation",
            "noninteractive": true,
            "owner": "wayfinding"
          }
        },
        "completion_condition": {
          "kind": "issue-closed",
          "target": {
            "kind": "issue",
            "repository": "<repository>",
            "number": "<research-ticket>"
          }
        },
        "safety_case": {
          "version": "1",
          "instruction": {"mode": "skill", "value": "/research <research-ticket>"},
          "target": {
            "kind": "issue",
            "repository": "<repository>",
            "number": "<research-ticket>"
          },
          "completion_condition": {
            "kind": "issue-closed",
            "target": {
              "kind": "issue",
              "repository": "<repository>",
              "number": "<research-ticket>"
            }
          },
          "assumptions": [
            {
              "kind": "no-human-decision",
              "statement": "A research ticket surfaces a fact from primary sources; it decides nothing."
            },
            {
              "kind": "durable-inputs-fixed",
              "statement": "The question is already fixed in the ticket body."
            },
            {
              "kind": "objective-completion",
              "statement": "The ticket is resolved exactly when it closes."
            }
          ],
          "effects": [
            {"kind": "network-read", "scope": "primary-sources"},
            {"kind": "repository-write", "scope": "<repository>"},
            {"kind": "tracker-write", "scope": "issue:<repository>#<research-ticket>"}
          ],
          "requirements": [
            {"kind": "skill", "name": "research"},
            {"kind": "access", "name": "tracker-write"}
          ],
          "retry": {"kind": "idempotent"},
          "triggers": []
        }
      },
      {
        "key": "prototype-<prototype-ticket>",
        "summary": "Make something concrete for ticket <prototype-ticket> to react to",
        "kind": "Prototype evidence",
        "occurrence": "v1",
        "instruction": {"mode": "skill", "value": "/prototype <prototype-ticket>"},
        "target": {
          "kind": "issue",
          "repository": "<repository>",
          "number": "<prototype-ticket>"
        },
        "basis": [
          {"kind": "issue", "repository": "<repository>", "number": "<map-issue>"}
        ],
        "prerequisites": [],
        "interaction": {
          "classification": "HITL-required",
          "evidence": {
            "kind": "human-boundary",
            "reason": "subjective-validation",
            "resolution_condition": {
              "kind": "issue-closed",
              "target": {
                "kind": "issue",
                "repository": "<repository>",
                "number": "<prototype-ticket>"
              }
            }
          }
        },
        "completion_condition": {
          "kind": "issue-closed",
          "target": {
            "kind": "issue",
            "repository": "<repository>",
            "number": "<prototype-ticket>"
          }
        }
      },
      {
        "key": "task-<task-ticket>",
        "summary": "Do the manual work ticket <task-ticket> is waiting on",
        "kind": "Perform manual validation",
        "occurrence": "v1",
        "instruction": {
          "mode": "manual",
          "value": "Work the checklist on ticket <task-ticket>, then record what was done and close it."
        },
        "target": {
          "kind": "issue",
          "repository": "<repository>",
          "number": "<task-ticket>"
        },
        "basis": [
          {"kind": "issue", "repository": "<repository>", "number": "<map-issue>"}
        ],
        "prerequisites": [
          {
            "kind": "dependency-satisfied",
            "target": {
              "kind": "issue",
              "repository": "<repository>",
              "number": "<blocking-ticket>"
            }
          }
        ],
        "interaction": {
          "classification": "HITL-required",
          "evidence": {
            "kind": "human-boundary",
            "reason": "credential-required",
            "resolution_condition": {
              "kind": "issue-closed",
              "target": {
                "kind": "issue",
                "repository": "<repository>",
                "number": "<task-ticket>"
              }
            }
          }
        },
        "completion_condition": {
          "kind": "issue-closed",
          "target": {
            "kind": "issue",
            "repository": "<repository>",
            "number": "<task-ticket>"
          }
        }
      },
      {
        "key": "chart-remaining-fog",
        "summary": "Chart the fog still in scope on map <map-issue>",
        "kind": "Chart workstream",
        "occurrence": "v1",
        "instruction": {"mode": "skill", "value": "/wayfinder <map-issue>"},
        "target": {
          "kind": "issue",
          "repository": "<repository>",
          "number": "<map-issue>"
        },
        "basis": [
          {"kind": "issue", "repository": "<repository>", "number": "<map-issue>"}
        ],
        "prerequisites": [
          {
            "kind": "dependency-satisfied",
            "target": {
              "kind": "issue",
              "repository": "<repository>",
              "number": "<grilling-ticket>"
            }
          },
          {
            "kind": "dependency-satisfied",
            "target": {
              "kind": "issue",
              "repository": "<repository>",
              "number": "<research-ticket>"
            }
          },
          {
            "kind": "dependency-satisfied",
            "target": {
              "kind": "issue",
              "repository": "<repository>",
              "number": "<prototype-ticket>"
            }
          },
          {
            "kind": "dependency-satisfied",
            "target": {
              "kind": "issue",
              "repository": "<repository>",
              "number": "<task-ticket>"
            }
          }
        ],
        "interaction": {
          "classification": "HITL-required",
          "evidence": {
            "kind": "human-boundary",
            "reason": "scope-ambiguity",
            "resolution_condition": {
              "kind": "issue-closed",
              "target": {
                "kind": "issue",
                "repository": "<repository>",
                "number": "<map-issue>"
              }
            }
          }
        },
        "completion_condition": {
          "kind": "issue-closed",
          "target": {
            "kind": "issue",
            "repository": "<repository>",
            "number": "<map-issue>"
          }
        }
      }
    ]
  }
}
```

Only the `research` shape is `AFK-safe`, and it is the only one that can be: the other three
ticket types are HITL by the [Ticket Types](#ticket-types) table, and a `Chart workstream` Action
is hard HITL because the fog is exactly what nobody can yet phrase. `Chart workstream` carries one
`dependency-satisfied` prerequisite per live ticket, because resolving them is what clears the fog
ahead — until then charting more of the map is guesswork, so it reads **Blocked**. It completes
when the map itself closes, because the map is done exactly when the way is clear.

**If `publish` fails, the work is repair-required, not done.** The map, its tickets and their
edges are already durable, so an exit-`1` result carrying `"code": "repair_required"` means the
transition happened but its record did not. Say so plainly, quote the message, and stop — do not
retry blindly, invent a record, or report the session as complete.

### Reaching the destination

A map is **not** finished because its Action list came out empty. It is finished when its
**Destination** is durably satisfied *and* nothing is left to decide — no open decision ticket and
no fog in **Not yet specified**. Until all three hold, keep publishing the charting record above:
an open ticket or a live `Chart workstream` Action is the map saying, truthfully, that the way is
not clear yet.

When all three do hold, what you publish depends on the destination this map named.

Either record is a **successor** to the charting record, not a second root: both land on the same
carrier under the same Anchor, and two roots there are a `revision_fork` that drops **both**
records from guidance. So publish it in two steps, copying runtime values from the first into the
second:

```bash
echo '{"repository":"<repository>","trusted_producers":["<producer-login>"],"revision_protocol":true}' \
  | git-loopy continuation reconcile
```

Take the whole `result.observation` object verbatim as the request's `observation`, and the
`revision_id` of every `result.observation.heads` entry whose `carrier` is the map as its
`parents`. Then retire, one receipt each, every Action the predecessor record carried — the
`action_key` values are the `key`s you published in the charting record. A successor that drops an
Action without a receipt leaves a `missing_retirement_receipt` diagnostic, which is guidance
saying, correctly, that it cannot account for what happened to it. The same two steps apply to a
re-chart from **Work through the map**: graduating fog into fresh tickets republishes this map's
record, and a second root would silently take the whole map out of guidance.

**A specification destination** — the map was finding its way to a spec — is not reached until the
spec exists, so publish a successor rather than a completion. The `Publish spec` Action hands the
map to `/to-spec`, which owns the next transition. Every predecessor Action retires as `completed`,
including `Chart workstream`: charting the fog *is* finished — the way is clear — even though the
map has not closed yet, and a receipt is how you say that when the completion condition cannot.

<!-- continuation-request: map-specification-destination -->
```json
{
  "repository": "<repository>",
  "trusted_producers": ["<producer-login>"],
  "observation": "<observation>",
  "parents": ["<predecessor-revision>"],
  "completion": {
    "continuation_contract_version": "1.2",
    "record_format": 1,
    "publication": "shared",
    "disposition": "continue",
    "workstream": {
      "anchor": {
        "kind": "issue",
        "repository": "<repository>",
        "number": "<map-issue>"
      },
      "destination": {
        "kind": "sub-issues-complete",
        "target": {
          "kind": "issue",
          "repository": "<repository>",
          "number": "<map-issue>"
        }
      }
    },
    "transition": {
      "owner": "wayfinding",
      "evidence": [
        {
          "kind": "issue-comment",
          "repository": "<repository>",
          "issue": "<map-issue>",
          "comment_id": "<evidence-comment>"
        }
      ]
    },
    "producer": {"login": "<producer-login>", "role": "planning"},
    "carrier": {
      "kind": "issue",
      "repository": "<repository>",
      "number": "<map-issue>"
    },
    "retirements": [
      {
        "predecessor_revision_id": "<predecessor-revision>",
        "action_key": "grilling-<grilling-ticket>",
        "reason": "completed",
        "evidence": [
            {
              "kind": "issue-comment",
              "repository": "<repository>",
              "issue": "<map-issue>",
              "comment_id": "<evidence-comment>"
            }
        ]
      },
      {
        "predecessor_revision_id": "<predecessor-revision>",
        "action_key": "research-<research-ticket>",
        "reason": "completed",
        "evidence": [
            {
              "kind": "issue-comment",
              "repository": "<repository>",
              "issue": "<map-issue>",
              "comment_id": "<evidence-comment>"
            }
        ]
      },
      {
        "predecessor_revision_id": "<predecessor-revision>",
        "action_key": "prototype-<prototype-ticket>",
        "reason": "completed",
        "evidence": [
            {
              "kind": "issue-comment",
              "repository": "<repository>",
              "issue": "<map-issue>",
              "comment_id": "<evidence-comment>"
            }
        ]
      },
      {
        "predecessor_revision_id": "<predecessor-revision>",
        "action_key": "task-<task-ticket>",
        "reason": "completed",
        "evidence": [
            {
              "kind": "issue-comment",
              "repository": "<repository>",
              "issue": "<map-issue>",
              "comment_id": "<evidence-comment>"
            }
        ]
      },
      {
        "predecessor_revision_id": "<predecessor-revision>",
        "action_key": "chart-remaining-fog",
        "reason": "completed",
        "evidence": [
            {
              "kind": "issue-comment",
              "repository": "<repository>",
              "issue": "<map-issue>",
              "comment_id": "<evidence-comment>"
            }
        ]
      }
    ],
    "actions": [
      {
        "key": "publish-spec",
        "summary": "Turn map <map-issue> into the spec it was finding its way to",
        "kind": "Publish spec",
        "occurrence": "v1",
        "instruction": {"mode": "skill", "value": "/to-spec <map-issue>"},
        "target": {
          "kind": "issue",
          "repository": "<repository>",
          "number": "<map-issue>"
        },
        "basis": [
          {
            "kind": "issue-comment",
            "repository": "<repository>",
            "issue": "<map-issue>",
            "comment_id": "<evidence-comment>"
          }
        ],
        "prerequisites": [
          {
            "kind": "sub-issues-complete",
            "target": {
              "kind": "issue",
              "repository": "<repository>",
              "number": "<map-issue>"
            }
          }
        ],
        "interaction": {
          "classification": "HITL-required",
          "evidence": {
            "kind": "human-boundary",
            "reason": "human-decision",
            "resolution_condition": {
              "kind": "issue-closed",
              "target": {
                "kind": "issue",
                "repository": "<repository>",
                "number": "<map-issue>"
              }
            }
          }
        },
        "completion_condition": {
          "kind": "issue-closed",
          "target": {
            "kind": "issue",
            "repository": "<repository>",
            "number": "<map-issue>"
          }
        }
      }
    ]
  }
}
```

`Publish spec` is `HITL-required` because `/to-spec` is `disable-model-invocation: true` — it
synthesises a spec from a conversation a human had. Publish this record for a **specification**
destination only. A decision destination or an in-place change has already arrived by the time the
way is clear, and inventing a spec step for it would put a whole `/to-spec` session in the human's
way for a spec nobody asked for.

**Any other destination**, durably satisfied, is terminal. Close the map, then publish:

<!-- continuation-request: map-complete -->
```json
{
  "repository": "<repository>",
  "trusted_producers": ["<producer-login>"],
  "observation": "<observation>",
  "parents": ["<predecessor-revision>"],
  "completion": {
    "continuation_contract_version": "1.2",
    "record_format": 1,
    "publication": "shared",
    "disposition": "terminal",
    "workstream": {
      "anchor": {
        "kind": "issue",
        "repository": "<repository>",
        "number": "<map-issue>"
      },
      "destination": {
        "kind": "sub-issues-complete",
        "target": {
          "kind": "issue",
          "repository": "<repository>",
          "number": "<map-issue>"
        }
      }
    },
    "transition": {
      "owner": "wayfinding",
      "evidence": [
        {
          "kind": "issue-comment",
          "repository": "<repository>",
          "issue": "<map-issue>",
          "comment_id": "<evidence-comment>"
        }
      ]
    },
    "producer": {"login": "<producer-login>", "role": "planning"},
    "carrier": {
      "kind": "issue",
      "repository": "<repository>",
      "number": "<map-issue>"
    },
    "retirements": [
      {
        "predecessor_revision_id": "<predecessor-revision>",
        "action_key": "grilling-<grilling-ticket>",
        "reason": "workstream-outcome",
        "evidence": [
            {
              "kind": "issue-comment",
              "repository": "<repository>",
              "issue": "<map-issue>",
              "comment_id": "<evidence-comment>"
            }
        ]
      },
      {
        "predecessor_revision_id": "<predecessor-revision>",
        "action_key": "research-<research-ticket>",
        "reason": "workstream-outcome",
        "evidence": [
            {
              "kind": "issue-comment",
              "repository": "<repository>",
              "issue": "<map-issue>",
              "comment_id": "<evidence-comment>"
            }
        ]
      },
      {
        "predecessor_revision_id": "<predecessor-revision>",
        "action_key": "prototype-<prototype-ticket>",
        "reason": "workstream-outcome",
        "evidence": [
            {
              "kind": "issue-comment",
              "repository": "<repository>",
              "issue": "<map-issue>",
              "comment_id": "<evidence-comment>"
            }
        ]
      },
      {
        "predecessor_revision_id": "<predecessor-revision>",
        "action_key": "task-<task-ticket>",
        "reason": "workstream-outcome",
        "evidence": [
            {
              "kind": "issue-comment",
              "repository": "<repository>",
              "issue": "<map-issue>",
              "comment_id": "<evidence-comment>"
            }
        ]
      },
      {
        "predecessor_revision_id": "<predecessor-revision>",
        "action_key": "chart-remaining-fog",
        "reason": "workstream-outcome",
        "evidence": [
            {
              "kind": "issue-comment",
              "repository": "<repository>",
              "issue": "<map-issue>",
              "comment_id": "<evidence-comment>"
            }
        ]
      }
    ],
    "outcome": {
      "kind": "complete",
      "destination_satisfied": true,
      "effective_at": "<rfc3339-utc>",
      "summary": "The way to the destination is clear: every decision ticket is closed and no fog remains.",
      "evidence": [
        {
          "kind": "issue-comment",
          "repository": "<repository>",
          "issue": "<map-issue>",
          "comment_id": "<evidence-comment>"
        }
      ]
    }
  }
}
```

`<rfc3339-utc>` is the moment the destination was reached, e.g. `date -u +%Y-%m-%dT%H:%M:%SZ`.
`destination_satisfied` is `true` only here: a map abandoned or redrawn is `abandoned` or
`superseded` with `destination_satisfied` `false`, because a redrawn destination is a fresh
effort, not this one arriving.

At the conclusion of a `/wayfinder` session, run the `/next` skill.