---
name: to-tickets
description: Break a plan, spec, or the current conversation into a set of tracer-bullet tickets, each declaring its blocking edges, published to the configured tracker — edges as text in one file per ticket locally, or native blocking links on a real tracker.
disable-model-invocation: true
---

# To Tickets

Break a plan, spec, or conversation into a set of **tickets** — tracer-bullet vertical slices, each declaring the tickets that **block** it.

The issue tracker and triage label vocabulary should have been provided to you — run `/setup-agent-skills` if not.

## Process

### 1. Gather context

Work from whatever is already in the conversation context. If the user passes a reference (a spec path, an issue number or URL) as an argument, fetch it and read its full body and comments.

### 2. Explore the codebase (optional)

If you have not already explored the codebase, do so to understand the current state of the code. Ticket titles and descriptions should use the project's domain glossary vocabulary, and respect ADRs in the area you're touching.

Look for opportunities to prefactor the code to make the implementation easier. "Make the change easy, then make the easy change."

### 3. Draft vertical slices

Break the work into **tracer bullet** tickets.

<vertical-slice-rules>

- Each slice cuts a narrow but COMPLETE path through every layer (schema, API, UI, tests) — vertical, NOT a horizontal slice of one layer
- A completed slice is demoable or verifiable on its own
- Each slice is sized to fit in a single fresh context window
- Any prefactoring should be done first

</vertical-slice-rules>

Give each ticket its **blocking edges** — the other tickets that must complete before it can start. A ticket with no blockers can start immediately.

**Wide refactors are the exception to vertical slicing.** A **wide refactor** is one mechanical change — rename a column, retype a shared symbol — whose **blast radius** fans across the whole codebase, so a single edit breaks thousands of call sites at once and no vertical slice can land green. Don't force it into a tracer bullet; sequence it as **expand–contract**. First expand: add the new form beside the old so nothing breaks. Then migrate the call sites over in batches sized by blast radius (per package, per directory), each batch its own ticket blocked by the expand, keeping CI green batch to batch because the old form still exists. Finally contract: delete the old form once no caller remains, in a ticket blocked by every migrate batch. When even the batches can't stay green alone, keep the sequence but let them share an integration branch that all block a final integrate-and-verify ticket — green is promised only there.

### 4. Quiz the user

Present the proposed breakdown as a numbered list. For each ticket, show:

- **Title**: short descriptive name
- **Blocked by**: which other tickets (if any) must complete first
- **What it delivers**: the end-to-end behaviour this ticket makes work

Ask the user:

- Does the granularity feel right? (too coarse / too fine)
- Are the blocking edges correct — does each ticket only depend on tickets that genuinely gate it?
- Should any tickets be merged or split further?

Iterate until the user approves the breakdown.

### 5. Publish the tickets to the configured tracker

Publish the approved tickets. **How** depends on the tracker `/setup-agent-skills` configured — the tickets are the same either way, only the shape of the blocking edges changes:

- **Local files** → write one file per ticket under `.scratch/<feature-slug>/issues/<NN>-<slug>.md`, numbered from `01` in dependency order (blockers first). Each file's "Blocked by" lists the numbers/titles it depends on. Use the per-ticket file template below — one ticket per file, never a single combined file.
- **A real issue tracker (GitHub, Linear, …)** → publish one issue per ticket in dependency order (blockers first) so each ticket's blocking edges can reference real identifiers. Use the platform's native blocking / sub-issue relationship where it has one; otherwise set each ticket's "Blocked by" to the blocking issues. Apply the `ready-for-agent` triage label unless instructed otherwise — the tickets are agent-grabbable by construction.

Work the **frontier**: any ticket whose blockers are all done. For a purely linear chain that means top to bottom.

Do NOT close or modify any parent issue.

<local-ticket-template>

# <NN> — <Ticket title>

**What to build:** the end-to-end behaviour this ticket makes work, from the user's perspective — not a layer-by-layer implementation list.

**Blocked by:** the numbers/titles of the tickets that gate this one, or "None — can start immediately".

**Status:** ready-for-agent

- [ ] Acceptance criterion 1
- [ ] Acceptance criterion 2

</local-ticket-template>

<issue-template>

## Parent

A reference to the parent issue on the tracker (if the source was an existing issue, otherwise omit this section).

## What to build

The end-to-end behaviour this ticket makes work, from the user's perspective — not layer-by-layer implementation.

## Acceptance criteria

- [ ] Criterion 1
- [ ] Criterion 2

## Blocked by

- A reference to each blocking ticket, or "None — can start immediately".

</issue-template>

In either form, avoid specific file paths or code snippets — they go stale fast. Exception: if a prototype produced a snippet that encodes a decision more precisely than prose can (state machine, reducer, schema, type shape), inline it and note briefly that it came from a prototype. Trim to the decision-rich parts — not a working demo, just the important bits.

Work the frontier one ticket at a time with `/implement`, clearing context between tickets.

## 6. Publish the decomposition transition

Decomposition is a durable workflow transition, and this skill owns it. Record it so the next
session — human or agent — reads the same answer you did.

**Publish only once the whole approved graph is durable.** The approved child set and its
edges are what make a leaf executable, so nothing is published until every one of them exists
natively:

1. Create **every** approved ticket, and link each one to the spec parent as a native
   **sub-issue**: `gh api --method POST repos/<owner>/<repo>/issues/<spec>/sub_issues -F
   sub_issue_id=<child-db-id>`, where `<child-db-id>` is the child's numeric database id
   (`gh api repos/<owner>/<repo>/issues/<n> --jq .id`, _not_ the `#number`).
2. Create **every** approved blocking edge as a native dependency: `gh api --method POST
   repos/<owner>/<repo>/issues/<child>/dependencies/blocked_by -F issue_id=<blocker-db-id>`.
   These native edges are the lifecycle fact readiness is derived from — a "Blocked by" line
   in the body is a fallback for trackers without dependencies, never the record.
3. Post one short transition-evidence comment on the spec parent naming the tickets you
   created (`gh issue comment <spec-issue> --body "..."`), and capture its comment id.
4. Only now publish: one request per ticket, then the parent-cleanup request. Never write a
   Continuation record, its `<!-- git-loopy-continuation... -->` marker, or its index label
   yourself — the command owns the carrier comment.

```bash
git-loopy continuation publish --input /tmp/implement-<ticket-issue>.json
```

If you cannot finish the graph, publish nothing. A partially published decomposition would
make a leaf executable against a plan that does not exist yet; the `artifact-exists`
prerequisites below are the second line of that defence, holding every published leaf
**Blocked** until its whole approved sibling set is durable.

Replace every `<placeholder>` with the durable identifier it names. Repeat the
`artifact-exists` prerequisite once per **other** approved ticket, and the
`dependency-satisfied` prerequisite once per native `blocked_by` blocker — a ticket with no
blockers carries none.

<!-- continuation-request: implement-leaf -->
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
        "number": "<ticket-issue>"
      },
      "destination": {
        "kind": "issue-closed",
        "target": {
          "kind": "issue",
          "repository": "<repository>",
          "number": "<ticket-issue>"
        }
      }
    },
    "transition": {
      "owner": "decomposition",
      "evidence": [
        {
          "kind": "issue-comment",
          "repository": "<repository>",
          "issue": "<spec-issue>",
          "comment_id": "<evidence-comment>"
        }
      ]
    },
    "producer": {"login": "<producer-login>", "role": "planning"},
    "carrier": {
      "kind": "issue",
      "repository": "<repository>",
      "number": "<ticket-issue>"
    },
    "actions": [
      {
        "key": "implement",
        "summary": "Implement ticket <ticket-issue>",
        "kind": "Implement ticket",
        "occurrence": "v1",
        "instruction": {"mode": "skill", "value": "/implement <ticket-issue>"},
        "target": {
          "kind": "issue",
          "repository": "<repository>",
          "number": "<ticket-issue>"
        },
        "basis": [
          {
            "kind": "issue",
            "repository": "<repository>",
            "number": "<spec-issue>"
          }
        ],
        "prerequisites": [
          {
            "kind": "artifact-exists",
            "target": {
              "kind": "issue",
              "repository": "<repository>",
              "number": "<sibling-issue>"
            }
          },
          {
            "kind": "dependency-satisfied",
            "target": {
              "kind": "issue",
              "repository": "<repository>",
              "number": "<blocking-issue>"
            }
          }
        ],
        "interaction": {
          "classification": "AFK-safe",
          "evidence": {
            "kind": "transition-owner-attestation",
            "noninteractive": true,
            "owner": "decomposition"
          }
        },
        "completion_condition": {
          "kind": "issue-closed",
          "target": {
            "kind": "issue",
            "repository": "<repository>",
            "number": "<ticket-issue>"
          }
        },
        "safety_case": {
          "version": "1",
          "instruction": {"mode": "skill", "value": "/implement <ticket-issue>"},
          "target": {
            "kind": "issue",
            "repository": "<repository>",
            "number": "<ticket-issue>"
          },
          "completion_condition": {
            "kind": "issue-closed",
            "target": {
              "kind": "issue",
              "repository": "<repository>",
              "number": "<ticket-issue>"
            }
          },
          "assumptions": [
            {
              "kind": "durable-inputs-fixed",
              "statement": "The approved child graph and its native dependencies are already durable."
            },
            {
              "kind": "objective-completion",
              "statement": "The ticket is done exactly when it closes."
            }
          ],
          "effects": [
            {"kind": "repository-write", "scope": "<repository>"},
            {"kind": "tracker-write", "scope": "issue:<repository>#<ticket-issue>"}
          ],
          "requirements": [
            {"kind": "skill", "name": "implement"},
            {"kind": "access", "name": "tracker-write"}
          ],
          "retry": {"kind": "resumable"},
          "triggers": []
        }
      }
    ]
  }
}
```

Closing the spec parent is **cleanup**, not delivery. It is its own low-priority Workstream —
anchored on the transition-evidence comment, so it neither proves the decomposition's
Destination nor gates any ticket — and it stays **Blocked** until every native sub-issue is
complete. Publish it once, last:

<!-- continuation-request: close-parent -->
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
        "kind": "issue-comment",
        "repository": "<repository>",
        "issue": "<spec-issue>",
        "comment_id": "<evidence-comment>"
      },
      "destination": {
        "kind": "issue-closed",
        "target": {
          "kind": "issue",
          "repository": "<repository>",
          "number": "<spec-issue>"
        }
      }
    },
    "transition": {
      "owner": "decomposition",
      "evidence": [
        {
          "kind": "issue-comment",
          "repository": "<repository>",
          "issue": "<spec-issue>",
          "comment_id": "<evidence-comment>"
        }
      ]
    },
    "producer": {"login": "<producer-login>", "role": "planning"},
    "carrier": {
      "kind": "issue",
      "repository": "<repository>",
      "number": "<spec-issue>"
    },
    "actions": [
      {
        "key": "close-parent",
        "summary": "Close the decomposed specification parent",
        "kind": "Close parent",
        "occurrence": "v1",
        "instruction": {"mode": "command", "value": "gh issue close <spec-issue>"},
        "target": {
          "kind": "issue",
          "repository": "<repository>",
          "number": "<spec-issue>"
        },
        "basis": [
          {
            "kind": "issue-comment",
            "repository": "<repository>",
            "issue": "<spec-issue>",
            "comment_id": "<evidence-comment>"
          }
        ],
        "prerequisites": [
          {
            "kind": "sub-issues-complete",
            "target": {
              "kind": "issue",
              "repository": "<repository>",
              "number": "<spec-issue>"
            }
          }
        ],
        "interaction": {
          "classification": "AFK-safe",
          "evidence": {
            "kind": "transition-owner-attestation",
            "noninteractive": true,
            "owner": "decomposition"
          }
        },
        "completion_condition": {
          "kind": "issue-closed",
          "target": {
            "kind": "issue",
            "repository": "<repository>",
            "number": "<spec-issue>"
          }
        },
        "safety_case": {
          "version": "1",
          "instruction": {"mode": "command", "value": "gh issue close <spec-issue>"},
          "target": {
            "kind": "issue",
            "repository": "<repository>",
            "number": "<spec-issue>"
          },
          "completion_condition": {
            "kind": "issue-closed",
            "target": {
              "kind": "issue",
              "repository": "<repository>",
              "number": "<spec-issue>"
            }
          },
          "assumptions": [
            {
              "kind": "no-human-decision",
              "statement": "Every approved child has closed, so closing the parent is bookkeeping."
            },
            {
              "kind": "objective-completion",
              "statement": "The cleanup is done exactly when the parent closes."
            }
          ],
          "effects": [
            {"kind": "tracker-write", "scope": "issue:<repository>#<spec-issue>"}
          ],
          "requirements": [
            {"kind": "command", "name": "gh"},
            {"kind": "access", "name": "tracker-write"}
          ],
          "retry": {"kind": "idempotent"},
          "triggers": []
        }
      }
    ]
  }
}
```

`<producer-login>` is the authenticated tracker account (`gh api user --jq .login`). Both
AFK-safe actions carry a safety case: an unattended claim with no argument behind it is a
guidance fault, and the runner will refuse to dispatch it.

**If `publish` fails, the work is repair-required, not done.** The tickets, their sub-issue
links, and their dependencies are already durable, so an exit-`1` result carrying
`"code": "repair_required"` means the decomposition happened but its record did not. Say so
plainly, name the tickets whose records are missing, and stop — do not retry blindly, invent a
record, or report the session as complete.

At the conclusion of a `/to-tickets` session, run the `/next` skill.