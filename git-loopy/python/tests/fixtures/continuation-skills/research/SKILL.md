---
name: research
description: Investigate a question against high-trust primary sources and capture the findings as a Markdown file in the repo. Use when the user wants a topic researched, docs or API facts gathered, or reading legwork delegated to a background agent.
---

Spin up a **background agent** to do the research, so you keep working while it reads.

Its job:

1. Investigate the question against **primary sources** — official docs, source code, specs, first-party APIs — not a secondary write-up of them. Follow every claim back to the source that owns it.
2. Always use the `/microsoft-docs` skill for anything related to Microsoft technologies such as Azure, Copilot Studio, etc. 
3. Write the findings to a single Markdown file, citing each claim's source.
4. Save it where the repo already keeps such notes; match the existing convention, and if there is none, put it somewhere sensible and say where.

## Owning the transition, or not

Most research is **nested**: another skill — `/wayfinder`, `/triage`, `/grill-with-docs` — needed
a fact before it could decide. Nested research owns no workflow transition. It publishes nothing;
it hands its durable pointers back to the skill that asked: the findings file's path, the commit
SHA that carries it, and the branch it lives on. That skill publishes once, for the transition it
owns.

Research owns a transition **only** when it was invoked directly on its own tracker ticket — a
`wayfinder:research` ticket, say — and durably resolves it. Then, and only then:

1. Commit the findings file.
2. Post the answer as a resolution comment on the ticket and **close the ticket**. That comment is
   the transition evidence; capture its comment id.
3. Publish through the native command. Never write the Continuation record, its
   `<!-- git-loopy-continuation... -->` marker, or its index label yourself — the command owns the
   carrier comment, and a hand-written one is not a record.

```bash
git-loopy continuation publish --input /tmp/publish-research.json
```

The ticket asked one question and the answer is now durable, so the record is **terminal**: the
ticket's own Destination is satisfied and it has no successor. `<producer-login>` is the
authenticated tracker account (`gh api user --jq .login`), `<findings-commit>` is the full
40-character SHA carrying the findings file, and `<rfc3339-utc>` is when the ticket closed
(`date -u +%Y-%m-%dT%H:%M:%SZ`).

<!-- continuation-request: research-ticket-resolved -->
```json
{
  "repository": "<repository>",
  "trusted_producers": ["<producer-login>"],
  "completion": {
    "continuation_contract_version": "1.2",
    "record_format": 1,
    "publication": "shared",
    "disposition": "terminal",
    "workstream": {
      "anchor": {
        "kind": "issue",
        "repository": "<repository>",
        "number": "<research-ticket>"
      },
      "destination": {
        "kind": "issue-closed",
        "target": {
          "kind": "issue",
          "repository": "<repository>",
          "number": "<research-ticket>"
        }
      }
    },
    "transition": {
      "owner": "research",
      "evidence": [
        {
          "kind": "issue-comment",
          "repository": "<repository>",
          "issue": "<research-ticket>",
          "comment_id": "<evidence-comment>"
        }
      ]
    },
    "producer": {"login": "<producer-login>", "role": "planning"},
    "carrier": {
      "kind": "issue",
      "repository": "<repository>",
      "number": "<research-ticket>"
    },
    "outcome": {
      "kind": "complete",
      "destination_satisfied": true,
      "effective_at": "<rfc3339-utc>",
      "summary": "The question this ticket asked is answered from primary sources and the findings are committed.",
      "evidence": [
        {
          "kind": "issue-comment",
          "repository": "<repository>",
          "issue": "<research-ticket>",
          "comment_id": "<evidence-comment>"
        },
        {
          "kind": "commit",
          "repository": "<repository>",
          "sha": "<findings-commit>"
        }
      ]
    }
  }
}
```

**If `publish` fails, the work is repair-required, not done.** The findings and the closed ticket
are already durable, so an exit-`1` result carrying `"code": "repair_required"` means the
transition happened but its record did not. Say so plainly, quote the message, and stop — do not
retry blindly, invent a record, or report the session as complete.

### When the question survives the session

Not every directly-owned ticket gets answered. If the reading is done, the partial findings are
committed and posted, and the question is still open, **leave the ticket open and publish anyway**.

That is a durable transition — the repo and the tracker both changed — with genuinely no successor
this Producer may name. Re-scoping a question that would not answer belongs to whoever charted it,
and inventing a next step here would be guidance nobody stands behind. Silence is not an option
either: the contract will not read a missing record as a result, so a transition with no record is
indistinguishable from a ticket nobody opened. `no-successor-created` is the positive claim that
this session looked and found nothing to hand on.

The record is **not** terminal — the Destination is `issue-closed` and the ticket is open — and it
carries no `outcome` and no Actions. `<findings-commit>` is the commit carrying what *was* learnt,
so the next session starts from it rather than from scratch.

<!-- continuation-request: research-ticket-unresolved -->
```json
{
  "repository": "<repository>",
  "trusted_producers": ["<producer-login>"],
  "completion": {
    "continuation_contract_version": "1.2",
    "record_format": 1,
    "publication": "shared",
    "disposition": "no-guidance",
    "workstream": {
      "anchor": {
        "kind": "issue",
        "repository": "<repository>",
        "number": "<research-ticket>"
      },
      "destination": {
        "kind": "issue-closed",
        "target": {
          "kind": "issue",
          "repository": "<repository>",
          "number": "<research-ticket>"
        }
      }
    },
    "transition": {
      "owner": "research",
      "evidence": [
        {
          "kind": "issue-comment",
          "repository": "<repository>",
          "issue": "<research-ticket>",
          "comment_id": "<evidence-comment>"
        }
      ]
    },
    "producer": {"login": "<producer-login>", "role": "planning"},
    "carrier": {
      "kind": "issue",
      "repository": "<repository>",
      "number": "<research-ticket>"
    },
    "no_guidance": {
      "reason": "no-successor-created",
      "summary": "The findings so far are committed, but the question this ticket asked is still open.",
      "references": [
        {
          "kind": "commit",
          "repository": "<repository>",
          "sha": "<findings-commit>"
        }
      ]
    }
  }
}
```

Once that record exists, it is the ticket's first word, and the session that finally answers the
question publishes the **successor** to it — the same `research-ticket-resolved` body above, plus
two request-level lineage fields. Two roots on one carrier are a `revision_fork` that drops both,
which would lose the partial finding *and* the answer. So reconcile first:

```bash
echo '{"repository":"<repository>","trusted_producers":["<producer-login>"],"revision_protocol":true}' \
  | git-loopy continuation reconcile
```

Take the whole `result.observation` object verbatim as the request's `observation`, and the
`revision_id` of every `result.observation.heads` entry whose `carrier` is this ticket as its
`parents`. Retire nothing: a no-guidance record publishes no Action, so there is no receipt to owe.

