---
name: to-spec
description: Turn the current conversation into a spec and publish it to the project issue tracker — no interview, just synthesis of what you've already discussed.
disable-model-invocation: true
---

This skill takes the current conversation context and codebase understanding and produces a spec. Do NOT interview the user — just synthesize what you already know.

The issue tracker and triage label vocabulary should have been provided to you — run `/setup-matt-pocock-skills` if not.

## Process

1. Explore the repo to understand the current state of the codebase, if you haven't already. Use the project's domain glossary vocabulary throughout the spec, and respect any ADRs in the area you're touching.

2. Sketch out the seams at which you're going to test the feature. Existing seams should be preferred to new ones. Use the highest seam possible. If new seams are needed, propose them at the highest point you can. The fewer seams across the codebase, the better - the ideal number is one.

Check with the user that these seams match their expectations.

3. Write the spec using the template below, then publish it to the project issue tracker. Apply the `ready-for-agent` triage label - no need for additional triage.

<spec-template>

## Problem Statement

The problem that the user is facing, from the user's perspective.

## Solution

The solution to the problem, from the user's perspective.

## User Stories

A LONG, numbered list of user stories. Each user story should be in the format of:

1. As an <actor>, I want a <feature>, so that <benefit>

<user-story-example>
1. As a mobile bank customer, I want to see balance on my accounts, so that I can make better informed decisions about my spending
</user-story-example>

This list of user stories should be extremely extensive and cover all aspects of the feature.

## Implementation Decisions

A list of implementation decisions that were made. This can include:

- The modules that will be built/modified
- The interfaces of those modules that will be modified
- Technical clarifications from the developer
- Architectural decisions
- Schema changes
- API contracts
- Specific interactions

Do NOT include specific file paths or code snippets. They may end up being outdated very quickly.

Exception: if a prototype produced a snippet that encodes a decision more precisely than prose can (state machine, reducer, schema, type shape), inline it within the relevant decision and note briefly that it came from a prototype. Trim to the decision-rich parts — not a working demo, just the important bits.

## Testing Decisions

A list of testing decisions that were made. Include:

- A description of what makes a good test (only test external behavior, not implementation details)
- Which modules will be tested
- Prior art for the tests (i.e. similar types of tests in the codebase)

## Out of Scope

A description of the things that are out of scope for this spec.

## Further Notes

Any further notes about the feature.

</spec-template>

4. Publish the transition (see below), then run the `/next` skill.

## Publish the specification transition

Publishing a spec is a durable workflow transition, and this skill owns it. Record it so the
next session — human or agent — reads the same answer you did.

**Only publish after the spec is durably published.** You need the real issue number of the
spec, and a durable comment on it recording the transition. In order:

1. Create the spec issue on the tracker (step 3 above). Everything below references it.
2. Post one short transition-evidence comment on that issue — what this transition changed and
   what it came from (`gh issue comment <spec-issue> --body "..."`). Capture its comment id:
   `gh api repos/<owner>/<repo>/issues/comments --jq '.[-1].id'`, or read the id from the
   comment URL you just created.
3. Build the completion request below and hand it to the native command. Never write the
   Continuation record, its `<!-- git-loopy-continuation... -->` marker, or its index label
   yourself — the command owns the carrier comment, and a hand-written one is not a record.

```bash
git-loopy continuation publish --input /tmp/publish-spec.json
```

A published spec is a **specification artifact**, not an executable ticket — it keeps the
`ready-for-agent` label so triage stays simple, and this record is what says the next step is
**decomposition** rather than implementation. Do not publish an `Implement ticket` action for
the spec parent.

Replace every `<placeholder>` with the durable identifier it names:

<!-- continuation-request: publish-spec -->
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
        "number": "<spec-issue>"
      },
      "destination": {
        "kind": "sub-issues-complete",
        "target": {
          "kind": "issue",
          "repository": "<repository>",
          "number": "<spec-issue>"
        }
      }
    },
    "transition": {
      "owner": "specification",
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
        "key": "decompose",
        "summary": "Decompose the specification into tracer-bullet tickets",
        "kind": "Decompose spec",
        "occurrence": "v1",
        "instruction": {"mode": "skill", "value": "/to-tickets <spec-issue>"},
        "target": {
          "kind": "issue",
          "repository": "<repository>",
          "number": "<spec-issue>"
        },
        "basis": [
          {
            "kind": "issue",
            "repository": "<repository>",
            "number": "<spec-issue>"
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
                "number": "<spec-issue>"
              }
            }
          }
        },
        "completion_condition": {
          "kind": "issue-closed",
          "target": {
            "kind": "issue",
            "repository": "<repository>",
            "number": "<spec-issue>"
          }
        }
      }
    ]
  }
}
```

`<producer-login>` is the authenticated tracker account (`gh api user --jq .login`).
Decomposition is `HITL-required` because the breakdown needs the user's approval before any
ticket exists.

**If `publish` fails, the work is repair-required, not done.** The spec issue and its comment
are already durable, so an exit-`1` result carrying `"code": "repair_required"` means the
transition happened but its record did not. Say so plainly, quote the message, and stop — do
not retry blindly, invent a record, or report the session as complete.