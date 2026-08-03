---
name: grill-with-docs
description: A relentless interview to sharpen a plan or design, which also creates docs (ADR's and glossary) as we go.
disable-model-invocation: true
---

Run a `/grilling` session, using the `/domain-modeling` skill.

## Publish the decision transition

A grilling session that lands a decision — a glossary term pinned, an ADR written, a question
answered — has made a durable workflow transition, and this skill owns it. Record it so the next
session reads the decision rather than re-litigating it.

**Only publish after the decision is durable.** In order:

1. Land the decision where it lives: the ADR under `docs/adr/` or the `CONTEXT.md` entry,
   committed.
2. Post one resolution comment on the issue or ticket being grilled, gisting the decision and
   linking the ADR. That comment *is* the transition evidence; capture its comment id.
3. Build the request below and hand it to the native command. Never write the Continuation
   record, its `<!-- git-loopy-continuation... -->` marker, or its index label yourself — the
   command owns the carrier comment, and a hand-written one is not a record.

```bash
git-loopy continuation publish --input /tmp/publish-decision.json
```

`<producer-login>` is the authenticated tracker account (`gh api user --jq .login`) and
`<decision-commit>` is the full 40-character SHA of the commit carrying the ADR or glossary
change.

Publish this record while the grilled issue still has an open question — one `Resolve decision`
Action naming it. Replace every `<placeholder>` with the durable identifier it names:

<!-- continuation-request: resolve-remaining-decision -->
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
        "number": "<grilled-issue>"
      },
      "destination": {
        "kind": "issue-closed",
        "target": {
          "kind": "issue",
          "repository": "<repository>",
          "number": "<grilled-issue>"
        }
      }
    },
    "transition": {
      "owner": "grill-with-docs",
      "evidence": [
        {
          "kind": "issue-comment",
          "repository": "<repository>",
          "issue": "<grilled-issue>",
          "comment_id": "<evidence-comment>"
        }
      ]
    },
    "producer": {"login": "<producer-login>", "role": "planning"},
    "carrier": {
      "kind": "issue",
      "repository": "<repository>",
      "number": "<grilled-issue>"
    },
    "actions": [
      {
        "key": "resolve-next-decision",
        "summary": "Resolve the next open question on issue <grilled-issue>",
        "kind": "Resolve decision",
        "occurrence": "v1",
        "instruction": {"mode": "skill", "value": "/grill-with-docs <grilled-issue>"},
        "target": {
          "kind": "issue",
          "repository": "<repository>",
          "number": "<grilled-issue>"
        },
        "basis": [
          {
            "kind": "issue-comment",
            "repository": "<repository>",
            "issue": "<grilled-issue>",
            "comment_id": "<evidence-comment>"
          },
          {
            "kind": "commit",
            "repository": "<repository>",
            "sha": "<decision-commit>"
          }
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
                "number": "<grilled-issue>"
              }
            }
          }
        },
        "completion_condition": {
          "kind": "issue-closed",
          "target": {
            "kind": "issue",
            "repository": "<repository>",
            "number": "<grilled-issue>"
          }
        }
      }
    ]
  }
}
```

The ADR commit is durable **Basis**, not a link in prose: the Action's grounds are the decision
that produced it, and a reader that cannot see the ADR cannot judge whether the question still
stands. `Resolve decision` is always `HITL-required` — an agent that answers its own grilling
questions has broken the whole point of the skill.

When the grilled issue has **no** open question left, close it and publish a terminal record
instead: `"disposition": "terminal"` with the `actions` array replaced by

```json
"outcome": {
  "kind": "complete",
  "destination_satisfied": true,
  "effective_at": "<rfc3339-utc>",
  "summary": "Every open question on this issue is decided and recorded.",
  "evidence": [
    {
      "kind": "issue-comment",
      "repository": "<repository>",
      "issue": "<grilled-issue>",
      "comment_id": "<evidence-comment>"
    },
    {"kind": "commit", "repository": "<repository>", "sha": "<decision-commit>"}
  ]
}
```

A grilling nested inside another skill's session — `/triage` step 4, a `/wayfinder` grilling
ticket — is **not** a transition of its own. It publishes nothing; it hands its durable pointers
(the resolution comment id, the ADR commit SHA) back to the skill that owns the transition, and
that skill publishes once.

**If `publish` fails, the work is repair-required, not done.** The ADR and its comment are already
durable, so an exit-`1` result carrying `"code": "repair_required"` means the transition happened
but its record did not. Say so plainly, quote the message, and stop — do not retry blindly, invent
a record, or report the session as complete.

At the conclusion of a `/grilling` session, run the `/next` skill.
