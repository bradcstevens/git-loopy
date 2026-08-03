---
name: resolving-merge-conflicts
description: "Use when you need to resolve an in-progress git merge/rebase conflict."
---

1. **See the current state** of the merge/rebase. Check git history, and the conflicting files.

2. **Find the primary sources** for each conflict. Understand deeply why each change was made, and what the original intent was. Read the commit messages, check the PRs, check original issues/tickets.

3. **Resolve each hunk.** Preserve both intents where possible. Where incompatible, pick the one matching the merge's stated goal and note the trade-off. Do **not** invent new behaviour. Always resolve; never `--abort`.

4. Discover the project's **automated checks** and run them — typically typecheck, then tests, then format. Fix anything the merge broke.

5. **Finish the merge/rebase.** Stage everything and commit. If rebasing, continue the rebase process until all commits are rebased.

6. **Publish the resolution transition.** Resolving a conflict is a durable workflow
   transition, and this skill owns it.

**Only publish once the resolved head is durable**: finish the merge or rebase, run the
project's checks, commit, and push, so the resolved head exists on the remote
(`git rev-parse HEAD`). Then post one short transition-evidence comment on the ticket
naming both sides of the reconciliation (`gh issue comment <ticket-issue> --body "..."`),
capture its comment id, and hand the completion request to the native command:

```bash
git-loopy continuation publish --input /tmp/publish-resolution.json
```

Never write the Continuation record, its `<!-- git-loopy-continuation... -->` marker, or its
index label yourself — the command owns the carrier comment.

A resolved head is a head **nobody has reviewed**: the resolution chose between two intents,
and that choice is exactly what review exists to check. So the successor is a fresh
`Review head` occurrence discriminated by the resolved head, never a reuse of the review the
pre-conflict head already passed. Replace every `<placeholder>` with the durable identifier
it names:

<!-- continuation-request: publish-resolution -->
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
        "issue": "<ticket-issue>",
        "comment_id": "<evidence-comment>"
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
      "owner": "conflict-resolution",
      "evidence": [
        {
          "kind": "issue-comment",
          "repository": "<repository>",
          "issue": "<ticket-issue>",
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
        "key": "review-resolved-head",
        "summary": "Review conflict-resolved head <resolved-head> for ticket <ticket-issue>",
        "kind": "Review head",
        "occurrence": "<resolved-head>",
        "instruction": {
          "mode": "skill",
          "value": "/code-review Review resolved head <resolved-head> on <branch-name> against ticket #<ticket-issue>, fixed point <remote-head>."
        },
        "target": {
          "kind": "commit",
          "repository": "<repository>",
          "sha": "<resolved-head>"
        },
        "basis": [
          {
            "kind": "issue",
            "repository": "<repository>",
            "number": "<ticket-issue>"
          },
          {
            "kind": "commit",
            "repository": "<repository>",
            "sha": "<resolved-head>"
          },
          {
            "kind": "issue-comment",
            "repository": "<repository>",
            "issue": "<ticket-issue>",
            "comment_id": "<evidence-comment>"
          }
        ],
        "prerequisites": [
          {
            "kind": "commit-exists",
            "target": {
              "kind": "commit",
              "repository": "<repository>",
              "sha": "<resolved-head>"
            }
          },
          {
            "kind": "branch-head-equals",
            "target": {
              "kind": "branch",
              "repository": "<repository>",
              "name": "<branch-name>",
              "sha": "<resolved-head>"
            }
          }
        ],
        "interaction": {
          "classification": "AFK-safe",
          "evidence": {
            "kind": "transition-owner-attestation",
            "noninteractive": true,
            "owner": "conflict-resolution"
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
          "instruction": {
            "mode": "skill",
            "value": "/code-review Review resolved head <resolved-head> on <branch-name> against ticket #<ticket-issue>, fixed point <remote-head>."
          },
          "target": {
            "kind": "commit",
            "repository": "<repository>",
            "sha": "<resolved-head>"
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
              "statement": "The resolved head and the remote head it reconciled are both durable commits."
            },
            {
              "kind": "no-human-decision",
              "statement": "The trade-off the resolution made is recorded in the evidence comment; reviewing it against both intents needs no further judgement call."
            },
            {
              "kind": "noninteractive-environment",
              "statement": "Reviewing a durable head reads the repository and the tracker and prompts for nothing."
            }
          ],
          "effects": [
            {"kind": "repository-read", "scope": "<repository>"},
            {"kind": "tracker-write", "scope": "issue:<repository>#<ticket-issue>"}
          ],
          "requirements": [
            {"kind": "skill", "name": "code-review"},
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

If the resolution needs authority you do not hold — a protected branch, a force push, a
credential — stop and publish the `authorize-operation` request documented in `/push`. Never
answer an authority prompt unattended, and never `--abort` around one.

**If `publish` fails, the work is repair-required, not done.** The resolution commit, the
push and the evidence comment are already durable, so an exit-`1` result carrying `"code":
"repair_required"` means the transition happened but its record did not. Say so plainly,
quote the message, and stop — do not retry blindly, and never hand-write the record the
command refused to write.
