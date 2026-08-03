---
name: implement
description: "Implement a piece of work based on a spec or set of tickets."
disable-model-invocation: true
---

Implement the work described by the user in the spec or tickets.

Use /tdd where possible, at pre-agreed seams.

Run typechecking regularly, single test files regularly, and the full test suite once at the end.

Once done and green, commit your work to the current branch and push it, then publish the
implementation transition below. The `Review head` Action it records is what routes that
exact candidate to `/code-review` — review the head you committed, never an uncommitted
worktree.

## Publish the implementation transition

Producing a candidate is a durable workflow transition, and this skill owns it. Record it so
the next session — human or agent — reads the same answer you did. `/tdd`,
`/codebase-design`, `/domain-modeling` and any documentation lookup run *inside* this
transition: they hand their evidence back here and publish nothing of their own.

**Only publish once the candidate head is durable.** A head that exists in one worktree is
not something a later reviewer can read, so, in order:

1. Finish the implementation and run the project's automated validation — typecheck, the
   tests covering the change, and the full suite once. A red loop is not a candidate.
2. Commit the work, then push the branch so the exact head is durable
   (`git rev-parse HEAD` is the candidate head; `git push` makes it readable).
3. Post one short transition-evidence comment on the ticket — what this transition changed
   and which head it produced (`gh issue comment <ticket-issue> --body "..."`). Capture its
   comment id from the URL you just created, or with
   `gh api repos/<owner>/<repo>/issues/comments --jq '.[-1].id'`.
4. Build the completion request below and hand it to the native command. Never write the
   Continuation record, its `<!-- git-loopy-continuation... -->` marker, or its index label
   yourself — the command owns the carrier comment, and a hand-written one is not a record.

```bash
git-loopy continuation publish --input /tmp/publish-implementation.json
```

The successor is a **review of that exact head**, never "review the branch": the occurrence
discriminator *is* the candidate head, so a remediated or conflict-resolved head is always a
new review occurrence that cannot inherit an earlier review's completion.

Replace every `<placeholder>` with the durable identifier it names. Keep the second Action
only when the ticket carries an acceptance criterion a human has to judge — subjective
acceptance is its own hard-HITL Action, never a promise folded into AFK-safe implementation.

<!-- continuation-request: publish-implementation -->
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
      "owner": "implementation",
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
        "key": "review-head",
        "summary": "Review candidate head <candidate-head> for ticket <ticket-issue>",
        "kind": "Review head",
        "occurrence": "<candidate-head>",
        "instruction": {
          "mode": "skill",
          "value": "/code-review Review <candidate-head> on <branch-name> against ticket #<ticket-issue>, fixed point <default-branch>."
        },
        "target": {
          "kind": "commit",
          "repository": "<repository>",
          "sha": "<candidate-head>"
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
            "sha": "<candidate-head>"
          }
        ],
        "prerequisites": [
          {
            "kind": "commit-exists",
            "target": {
              "kind": "commit",
              "repository": "<repository>",
              "sha": "<candidate-head>"
            }
          },
          {
            "kind": "branch-head-equals",
            "target": {
              "kind": "branch",
              "repository": "<repository>",
              "name": "<branch-name>",
              "sha": "<candidate-head>"
            }
          }
        ],
        "interaction": {
          "classification": "AFK-safe",
          "evidence": {
            "kind": "transition-owner-attestation",
            "noninteractive": true,
            "owner": "implementation"
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
            "value": "/code-review Review <candidate-head> on <branch-name> against ticket #<ticket-issue>, fixed point <default-branch>."
          },
          "target": {
            "kind": "commit",
            "repository": "<repository>",
            "sha": "<candidate-head>"
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
              "statement": "The reviewed head is one fixed durable commit, and the ticket it is reviewed against is already published."
            },
            {
              "kind": "no-human-decision",
              "statement": "Standards and spec conformance are read off documented sources; anything needing human judgement is the separate Perform manual validation Action."
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
      },
      {
        "key": "manual-validation",
        "summary": "Manually validate candidate head <candidate-head> against ticket <ticket-issue>",
        "kind": "Perform manual validation",
        "occurrence": "<candidate-head>",
        "instruction": {
          "mode": "manual",
          "value": "Run <branch-name> at <candidate-head> and judge the acceptance criteria of #<ticket-issue> that automated validation cannot decide."
        },
        "target": {
          "kind": "commit",
          "repository": "<repository>",
          "sha": "<candidate-head>"
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
            "sha": "<candidate-head>"
          }
        ],
        "prerequisites": [
          {
            "kind": "commit-exists",
            "target": {
              "kind": "commit",
              "repository": "<repository>",
              "sha": "<candidate-head>"
            }
          },
          {
            "kind": "branch-head-equals",
            "target": {
              "kind": "branch",
              "repository": "<repository>",
              "name": "<branch-name>",
              "sha": "<candidate-head>"
            }
          }
        ],
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
                "number": "<ticket-issue>"
              }
            }
          }
        },
        "completion_condition": {
          "kind": "issue-closed",
          "target": {
            "kind": "issue",
            "repository": "<repository>",
            "number": "<ticket-issue>"
          }
        }
      }
    ]
  }
}
```

`<producer-login>` is the authenticated tracker account (`gh api user --jq .login`);
`<branch-name>` is the branch carrying the candidate and `<default-branch>` the base it will
merge into.

Never widen your own authority to finish a candidate. If the work needs a login, MFA, a
secret, a consent prompt, or any privilege you do not already hold, stop and publish the
`authorize-operation` request documented in `/push` instead — unattended execution never
answers an authority prompt.

**If `publish` fails, the work is repair-required, not done.** The commit, the push and the
evidence comment are already durable, so an exit-`1` result carrying `"code":
"repair_required"` means the transition happened but its record did not. Say so plainly,
quote the message, and stop — do not retry blindly, and never hand-write the record the
command refused to write.
