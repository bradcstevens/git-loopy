---
name: prototype
description: Build a throwaway prototype to answer a design question. Use when the user wants to sanity-check whether a state model or logic feels right, or explore what a UI should look like.
---

# Prototype

A prototype is **throwaway code that answers a question**. The question decides the shape.

## Pick a branch

Identify which question is being answered — from the user's prompt, the surrounding code, or by asking if the user is around:

- **"Does this logic / state model feel right?"** → [LOGIC.md](LOGIC.md). Build a single shareable HTML file — free-play buttons plus tabbed guided walkthroughs — that pushes the state machine through cases that are hard to reason about on paper, and that a non-developer can drive.
- **"What should this look like?"** → [UI.md](UI.md). Generate several radically different UI variations on a single route, switchable via a URL search param and a floating bottom bar.

The two branches produce very different artifacts — getting this wrong wastes the whole prototype. If the question is genuinely ambiguous and the user isn't reachable, default to whichever branch better matches the surrounding code (a backend module → logic; a page or component → UI) and state the assumption at the top of the prototype.

## Rules that apply to both

1. **Throwaway from day one, and clearly marked as such.** Locate the prototype code close to where it will actually be used (next to the module or page it's prototyping for) so context is obvious — but name it so a casual reader can see it's a prototype, not production. For throwaway UI routes, obey whatever routing convention the project already uses; don't invent a new top-level structure.
2. **Trivial to run.** A UI prototype starts from one command in the project's task runner — `pnpm <name>`, `python <path>`, `bun <path>`, etc. A logic demo is a single HTML file the user double-clicks. Either way, no thinking required to start it.
3. **No persistence by default.** State lives in memory. Persistence is the thing the prototype is _checking_, not something it should depend on. If the question explicitly involves a database, hit a scratch DB or a local file with a clear "PROTOTYPE — wipe me" name.
4. **Skip the polish.** No tests, no error handling beyond what makes the prototype _runnable_, no abstractions. The point is to learn something fast.
5. **Surface the state.** After every action (logic) or on every variant switch (UI), print or render the full relevant state so the user can see what changed.
6. **Capture it when done.** Fold any validated decision into the real code, then capture the prototype itself as a **primary source**: commit it to a throwaway branch, out of main, and leave a context pointer to that branch on the implementation issue. Capture the answer too — the verdict and the question it settled — in the issue or a commit. The main branch keeps only the validated decision.

## Owning the transition, or not

Most prototypes are **nested**: another skill — `/wayfinder`, `/grill-with-docs`, a tracer bullet
under `/implement` — needed something concrete before it could decide. A nested prototype owns no
workflow transition. It publishes nothing; it hands its durable pointers back to the skill that
asked: the throwaway branch name and head SHA, and the comment recording the verdict. That skill
publishes once, for the transition it owns.

A prototype owns a transition **only** when it was invoked directly on its own tracker ticket — a
`wayfinder:prototype` ticket, say — and durably resolves it. Then, and only then:

1. Push the throwaway branch and note its head SHA.
2. Post the verdict as a resolution comment on the ticket and **close the ticket**. That comment
   is the transition evidence; capture its comment id.
3. Publish through the native command. Never write the Continuation record, its
   `<!-- git-loopy-continuation... -->` marker, or its index label yourself — the command owns the
   carrier comment, and a hand-written one is not a record.

```bash
git-loopy continuation publish --input /tmp/publish-prototype.json
```

The ticket asked one question and the verdict is now durable, so the record is **terminal**: the
ticket's own Destination is satisfied and it has no successor. `<producer-login>` is the
authenticated tracker account (`gh api user --jq .login`), `<prototype-branch>` and
`<prototype-branch-sha>` name the throwaway branch and its 40-character head SHA, and
`<rfc3339-utc>` is when the ticket closed (`date -u +%Y-%m-%dT%H:%M:%SZ`).

<!-- continuation-request: prototype-ticket-resolved -->
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
        "number": "<prototype-ticket>"
      },
      "destination": {
        "kind": "issue-closed",
        "target": {
          "kind": "issue",
          "repository": "<repository>",
          "number": "<prototype-ticket>"
        }
      }
    },
    "transition": {
      "owner": "prototype",
      "evidence": [
        {
          "kind": "issue-comment",
          "repository": "<repository>",
          "issue": "<prototype-ticket>",
          "comment_id": "<evidence-comment>"
        }
      ]
    },
    "producer": {"login": "<producer-login>", "role": "planning"},
    "carrier": {
      "kind": "issue",
      "repository": "<repository>",
      "number": "<prototype-ticket>"
    },
    "outcome": {
      "kind": "complete",
      "destination_satisfied": true,
      "effective_at": "<rfc3339-utc>",
      "summary": "The prototype answered this ticket's question and the branch that proves it is pushed.",
      "evidence": [
        {
          "kind": "issue-comment",
          "repository": "<repository>",
          "issue": "<prototype-ticket>",
          "comment_id": "<evidence-comment>"
        },
        {
          "kind": "branch",
          "repository": "<repository>",
          "name": "<prototype-branch>",
          "sha": "<prototype-branch-sha>"
        }
      ]
    }
  }
}
```

The branch is the evidence because the prototype is throwaway by rule 1: the code will not be on
`main`, so a reference to it is the only durable thing the verdict can stand on.

**If `publish` fails, the work is repair-required, not done.** The branch and the closed ticket
are already durable, so an exit-`1` result carrying `"code": "repair_required"` means the
transition happened but its record did not. Say so plainly, quote the message, and stop — do not
retry blindly, invent a record, or report the session as complete.

### When the question survives the session

Not every directly-owned ticket gets settled. If the prototype ran, the branch is pushed and the
verdict comment is posted, but the question is still open — the experiment was inconclusive, or it
answered a smaller question than the ticket asked — **leave the ticket open and publish anyway**.

That is a durable transition — the branch and the tracker both changed — with genuinely no
successor this Producer may name. Re-scoping a question the prototype could not settle belongs to
whoever charted it, and inventing a next step here would be guidance nobody stands behind. Silence
is not an option either: the contract will not read a missing record as a result, so a transition
with no record is indistinguishable from a ticket nobody opened. `no-successor-created` is the
positive claim that this session tried and found nothing to hand on.

The record is **not** terminal — the Destination is `issue-closed` and the ticket is open — and it
carries no `outcome` and no Actions. The branch is still the reference, because a prototype that
settled nothing is exactly the one whose code the next session wants to read.

<!-- continuation-request: prototype-ticket-unresolved -->
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
        "number": "<prototype-ticket>"
      },
      "destination": {
        "kind": "issue-closed",
        "target": {
          "kind": "issue",
          "repository": "<repository>",
          "number": "<prototype-ticket>"
        }
      }
    },
    "transition": {
      "owner": "prototype",
      "evidence": [
        {
          "kind": "issue-comment",
          "repository": "<repository>",
          "issue": "<prototype-ticket>",
          "comment_id": "<evidence-comment>"
        }
      ]
    },
    "producer": {"login": "<producer-login>", "role": "planning"},
    "carrier": {
      "kind": "issue",
      "repository": "<repository>",
      "number": "<prototype-ticket>"
    },
    "no_guidance": {
      "reason": "no-successor-created",
      "summary": "The prototype ran and its branch is pushed, but the question this ticket asked is still open.",
      "references": [
        {
          "kind": "branch",
          "repository": "<repository>",
          "name": "<prototype-branch>",
          "sha": "<prototype-branch-sha>"
        }
      ]
    }
  }
}
```

Once that record exists, it is the ticket's first word, and the session that finally settles the
question publishes the **successor** to it — the same `prototype-ticket-resolved` body above, plus
two request-level lineage fields. Two roots on one carrier are a `revision_fork` that drops both,
which would lose the inconclusive run *and* the verdict. So reconcile first:

```bash
echo '{"repository":"<repository>","trusted_producers":["<producer-login>"],"revision_protocol":true}' \
  | git-loopy continuation reconcile
```

Take the whole `result.observation` object verbatim as the request's `observation`, and the
`revision_id` of every `result.observation.heads` entry whose `carrier` is this ticket as its
`parents`. Retire nothing: a no-guidance record publishes no Action, so there is no receipt to owe.

At the conclusion of a `/prototype` session, run the `/continuation` skill.
