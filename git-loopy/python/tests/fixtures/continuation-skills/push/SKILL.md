---
name: push
description: Publish current work by staging intended changes, committing, pushing, and opening a pull request when needed.
disable-model-invocation: true
---

# Publish the Current Work

Treat invocation as approval to persist the work in scope from the current
conversation. Preserve unrelated worktree changes. Honor any commit message,
remote, base branch, or pull-request preference supplied by the user.

## 1. Establish the publication state

Inspect:

- `git status --short`
- staged and unstaged diffs
- the current branch and upstream
- configured remotes and the remote default branch
- commits ahead of the upstream

A detached `HEAD` needs a user-selected branch before publication. For an active
merge, rebase, or cherry-pick conflict, stop and recommend
`/resolving-merge-conflicts`.

This step is complete when the intended paths, branch, remote, upstream state,
and existing local commits are known.

## 2. Gate on validation

Reuse successful validation from the current session when it still covers the
current diff. If the work changed afterward, run the smallest existing tests,
checks, or build commands that cover it. Resolve failures before publication.

This step is complete when the exact work being published has current passing
validation, or consists only of documentation with no repository-specific docs
check.

## 3. Stage exactly the intended change

Stage explicit in-scope paths with `git add -- <paths>`. Use `git add -A` only
when every dirty path belongs to this work. Inspect `git diff --cached` and
`git status --short` after staging, including untracked files and accidental
credentials.

This step is complete when the index contains all and only the intended change;
unrelated worktree changes remain unstaged.

## 4. Create the commit

When the index is non-empty, derive a concise commit message from the staged diff
and recent repository history, while honoring user-supplied wording and required
trailers. Let commit hooks run and resolve their failures.

When the index is empty, continue only if the branch already has unpublished
commits. Otherwise report that there is nothing to publish.

This step is complete when `HEAD` contains the intended change, the index is
clear of it, and any remaining worktree diff is identified as unrelated.

## 5. Push the branch

Use a normal fast-forward push:

- Existing upstream: `git push`
- No upstream: choose the configured remote, preferring `origin`, then run
  `git push -u <remote> <branch>`

With no configured remote, report the publication blocker. With multiple remotes
and no upstream or `origin`, ask the user to choose the destination.

If the remote rejects the push, fetch and report the divergence while leaving
history intact. A force push requires separate, explicit user approval.

This step is complete when the remote branch resolves to the local `HEAD`.

## 6. Resolve the pull request

For a GitHub remote on a non-default branch, use `gh` to find an open pull
request for the branch. Return its URL if one exists; otherwise create one
against the remote default branch using the commit range, validation results,
and relevant issue references for the title and body. A default-branch push or
non-GitHub remote makes a pull request inapplicable.

This step is complete when the pull-request URL is known or its inapplicability
is established.

## 7. Report the durable result

Report the commit SHA and subject, remote branch, pull-request URL or status, and
any unrelated changes left in the worktree.

Publication is complete only when the remote branch matches local `HEAD` and the
pull-request requirement is resolved.

## 8. Publish the head transition

Publishing a head is a durable workflow transition, and this skill owns it. Record it so the
next session — human or agent — reads the same answer you did.

**Only publish after step 7's durable result exists.** The remote branch must already
resolve to the head you are recording, and the pull request must already exist; a record
written from an intent rather than from a result would authorize a successor for work that
never landed. Then post one short transition-evidence comment on the ticket
(`gh issue comment <ticket-issue> --body "..."`), capture its comment id, and hand the
completion request to the native command:

```bash
git-loopy continuation publish --input /tmp/publish-head.json
```

Never write the Continuation record, its `<!-- git-loopy-continuation... -->` marker, or its
index label yourself — the command owns the carrier comment.

A **default-branch** publication has no pull request and therefore no merge boundary: the
head is already integrated, so publish nothing from it and let the ticket's own lifecycle
carry the Workstream. Everything below is for a non-default branch.

### The head is published

The successor is a human's: merging is a judgement about whether this change should be in
the default branch, and no attestation makes that unattended. Replace every `<placeholder>`
with the durable identifier it names:

<!-- continuation-request: publish-head -->
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
      "owner": "head-publication",
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
        "key": "review-and-merge",
        "summary": "Review and merge pull request <pull-request> at head <candidate-head>",
        "kind": "Review and merge PR",
        "occurrence": "<candidate-head>",
        "instruction": {
          "mode": "manual",
          "value": "Review pull request #<pull-request> at head <candidate-head> and merge it into <default-branch> if you accept it."
        },
        "target": {
          "kind": "pull-request",
          "repository": "<repository>",
          "number": "<pull-request>"
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
            "kind": "pull-request-open",
            "target": {
              "kind": "pull-request",
              "repository": "<repository>",
              "number": "<pull-request>"
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
            "reason": "human-decision",
            "resolution_condition": {
              "kind": "pull-request-merged",
              "target": {
                "kind": "pull-request",
                "repository": "<repository>",
                "number": "<pull-request>"
              }
            }
          }
        },
        "completion_condition": {
          "kind": "pull-request-merged",
          "target": {
            "kind": "pull-request",
            "repository": "<repository>",
            "number": "<pull-request>"
          }
        }
      }
    ]
  }
}
```

The merge Action is pinned to the head that was published: if the branch moves afterwards it
goes **Blocked**, because the head a human agreed to merge is no longer the head that would
be merged. Publish the new head through steps 1–7 again rather than reusing this Action.

### The remote rejected the publication

A divergence is durable git evidence, and it names its own resolver. Publish this instead of
the merge Action — never force-push around it, and never publish a merge boundary for work
that is not on the remote:

<!-- continuation-request: resolve-conflict -->
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
      "owner": "head-publication",
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
        "key": "resolve-conflict",
        "summary": "Reconcile <branch-name> with remote head <remote-head>",
        "kind": "Resolve conflict",
        "occurrence": "<remote-head>",
        "instruction": {
          "mode": "skill",
          "value": "/resolving-merge-conflicts Reconcile <branch-name> with remote head <remote-head> for #<ticket-issue>."
        },
        "target": {
          "kind": "branch",
          "repository": "<repository>",
          "name": "<branch-name>",
          "sha": "<remote-head>"
        },
        "basis": [
          {
            "kind": "issue",
            "repository": "<repository>",
            "number": "<ticket-issue>"
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
            "kind": "branch-head-equals",
            "target": {
              "kind": "branch",
              "repository": "<repository>",
              "name": "<branch-name>",
              "sha": "<remote-head>"
            }
          }
        ],
        "interaction": {
          "classification": "AFK-safe",
          "evidence": {
            "kind": "transition-owner-attestation",
            "noninteractive": true,
            "owner": "head-publication"
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
            "value": "/resolving-merge-conflicts Reconcile <branch-name> with remote head <remote-head> for #<ticket-issue>."
          },
          "target": {
            "kind": "branch",
            "repository": "<repository>",
            "name": "<branch-name>",
            "sha": "<remote-head>"
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
              "statement": "Both sides of the reconciliation are durable commits, and the remote head is named exactly."
            },
            {
              "kind": "bounded-effect-scope",
              "statement": "Resolution rewrites only this branch's working tree and history; it never force-pushes and never merges the pull request."
            },
            {
              "kind": "noninteractive-environment",
              "statement": "Resolution reads both intents from the repository and its tracker and prompts for nothing."
            }
          ],
          "effects": [
            {"kind": "repository-write", "scope": "<repository>"},
            {"kind": "git-write", "scope": "branch:<branch-name>"}
          ],
          "requirements": [
            {"kind": "skill", "name": "resolving-merge-conflicts"},
            {"kind": "access", "name": "repository-write"}
          ],
          "retry": {"kind": "resumable"},
          "triggers": []
        }
      }
    ]
  }
}
```

### The publication needs authority you do not have

A protected branch, a missing scope, an expired login, a required MFA challenge, a secret you
were never given, or any consent prompt is an **authority boundary**, not a failure to retry.
Unattended execution never answers one: stop, publish the Action below, and say plainly what
is blocked. This is the request every other Transition owner uses too when its work hits an
authority wall.

Set `reason` to the boundary you actually hit — `credential-required` for a login, MFA
challenge, or missing secret, `consent-required` for a consent prompt, `privilege-expansion`
for a scope or protection you do not hold:

<!-- continuation-request: authorize-operation -->
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
      "owner": "head-publication",
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
        "key": "authorize-operation",
        "summary": "Authorize publication of <branch-name> at <candidate-head>",
        "kind": "Authorize operation",
        "occurrence": "<candidate-head>",
        "instruction": {
          "mode": "manual",
          "value": "Grant the authority publication of <branch-name> at <candidate-head> needs for #<ticket-issue>, or publish it yourself."
        },
        "target": {
          "kind": "branch",
          "repository": "<repository>",
          "name": "<branch-name>",
          "sha": "<candidate-head>"
        },
        "basis": [
          {
            "kind": "issue",
            "repository": "<repository>",
            "number": "<ticket-issue>"
          },
          {
            "kind": "issue-comment",
            "repository": "<repository>",
            "issue": "<ticket-issue>",
            "comment_id": "<evidence-comment>"
          }
        ],
        "prerequisites": [],
        "interaction": {
          "classification": "HITL-required",
          "evidence": {
            "kind": "human-boundary",
            "reason": "privilege-expansion",
            "resolution_condition": {
              "kind": "branch-head-equals",
              "target": {
                "kind": "branch",
                "repository": "<repository>",
                "name": "<branch-name>",
                "sha": "<candidate-head>"
              }
            }
          }
        },
        "completion_condition": {
          "kind": "branch-head-equals",
          "target": {
            "kind": "branch",
            "repository": "<repository>",
            "name": "<branch-name>",
            "sha": "<candidate-head>"
          }
        }
      }
    ]
  }
}
```

**If `publish` fails, the work is repair-required, not done.** The push, the pull request and
the evidence comment are already durable, so an exit-`1` result carrying `"code":
"repair_required"` means the transition happened but its record did not. Say so plainly,
quote the message, and stop — do not retry blindly, and never hand-write the record the
command refused to write.
