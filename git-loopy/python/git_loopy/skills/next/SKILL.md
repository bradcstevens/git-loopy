---
name: next
description: Route workflow continuation from live project state. Use when a workflow skill concludes or the user asks what to do next.
---

# Route the Workflow

This skill is a read-only **Consumer**. It does not derive **Continuation
guidance** — the native Reconciliation command does, from the durable **Producer
revisions** each **Transition owner** published. Your job is to bind one request,
run one command, and present exactly what came back.

Do not reconstruct the answer. Normalization, trust, **Action identity**,
ordering, **Readiness**, and the Waiting/guidance/Complete status are all derived
by `reconcile`; re-deriving any of them here would be a second answer to a
question the contract already answers.

## 1. Bind the request

Two values are needed.

**Repository.** `gh repo view --json nameWithOwner -q .nameWithOwner`, in
`owner/name` form.

**Trusted producers.** The allowlist of logins whose records may be read as
guidance. This is a trust ceiling, so it is always an explicit input: take the
logins the operator names in this session or in the repository's own documented
Continuation trust policy. There is no Config surface for it yet. If nobody has
named one, ask the user and stop — never infer an allowlist from collaborators,
comment authors, or the records you can see. Add `"trusted_apps": ["<login>"]`
for a bot or App producer; a Bot author outside that list is ignored.

Write this request to a temporary file:

<!-- continuation-request: refresh -->
```json
{
  "repository": "<repository>",
  "trusted_producers": ["<trusted-producer>"],
  "revision_protocol": true
}
```

`revision_protocol: true` is what buys closed coverage, so it is not optional
here: without it Reconciliation reads a label index that can never prove terminal
completion, can never project a **Retirement receipt**, and can never see a
**Continuation conflict** between competing **Producer revisions**.

Never add an `automation` key. That block is Dispatch authorization, not a human
refresh, and `/next` authorizes nothing.

This step is complete when the request names a repository and a non-empty,
explicitly sourced trusted-producer allowlist.

## 2. Check what the installed distribution can answer

```bash
git-loopy continuation capabilities
```

`git-loopy` may be any member of the Runner family, and several fields this Skill
uses are optional capabilities that fail closed rather than being ignored. Read
`capabilities.optional_capabilities` once and narrow accordingly:

| Capability | What to drop when it is `false` |
| --- | --- |
| `immutable_producer_revisions` | `revision_protocol` — and say so: without it, Complete and **Retirement receipts** are out of reach for this refresh. |
| `prospective_projection` | `previous_actions` and `handoff` (§5 and §6). |
| `terminal_rendering` | `--terminal`; read the machine result and present it in the same shape. |

Narrow the request; never work around the gap by deriving the missing field
yourself.

This step is complete when the request contains only fields this distribution
advertises.

## 3. Run one Reconciliation

```bash
git-loopy continuation reconcile --terminal --input <request-file>
```

Exit `0` prints the locked human projection on stdout; that rendering is the
answer. Exit `1` prints one typed JSON error object instead — report its `code`
and `message` and stop. A failed refresh is not permission to fall back to
reading issues, labels, and branches yourself: guidance you reconstructed is not
guidance, and silently downgrading to it is the failure this Skill exists to
remove.

Run `reconcile` at most once per answer. It replaces the whole projection from
one stable read, so a second call is a different observation, not more of this
one.

This step is complete when one command has returned a typed result.

## 4. Present the projection

Show the rendered output. Keep its structure and its wording:

- **Primary Action** — one verified Action in full: **Readiness**, summary,
  interaction classification, **Instruction**, **Target**, and **Basis**.
- **Ready** and **Blocked** remainders — each states its full count and how many
  rows were withheld.
- **Needs attention** — diagnostics: conflicts, malformed guidance, unstable
  reads, revoked permissions, a carrier missing its discovery label. These are
  separate from the frontier and never reorder it. Report every one and repair
  none — a missing label and an unread fact are the Producer's to fix, and an
  Action whose facts would not stabilize leaves the frontier rather than being
  guessed Ready or Blocked.
- **Outcomes** — explicit terminal **Workstream outcomes**.

Copy the Instruction verbatim, on one physical line, in its own code fence so it
can be pasted straight into a terminal. Every **Target** and **Basis** is a
durable link; never paste the artifact's content in its place.

Do not re-rank, re-word, merge, split, or filter the Actions. The order is the
contract's **Continuation view** order.

To expand a withheld remainder, run the same request again without `--terminal`
and read `result.actions`. That is still read-only.

This step is complete when the projection has been presented as returned.

## 5. Carry a refresh delta, and only from this session

When this session already ran a Reconciliation, hand its own `result.actions`
back as `previous_actions` and the next refresh returns a bounded `delta` of
added, retired, and changed **Action identities**:

<!-- continuation-request: refresh-delta -->
```json
{
  "repository": "<repository>",
  "trusted_producers": ["<trusted-producer>"],
  "revision_protocol": true,
  "previous_actions": [
    {
      "identity": "<previous-action-identity>",
      "semantic_fingerprint": "<previous-semantic-fingerprint>"
    }
  ]
}
```

Reconciliation keeps no memory of past calls, so `previous_actions` is the only
source of "previous". Take the pairs from the earlier result in *this*
conversation. Never from a file, a cache, or a summary — a delta against
someone else's observation is a claim about a change that may never have
happened.

The delta is a footnote, not the answer. The projection returned now replaces
the previous one entirely; a **Retirement receipt** under `Retired this refresh`
is transient evidence that an Action left guidance, never a history to keep.
An Action whose **Readiness** moved is the same **Action occurrence** and appears
in no delta group.

This step is complete when a delta has been reported, or omitted because this
session has no prior projection.

## 6. Attach a resume pointer only for the exact occurrence

When this session holds machine-local **Handoff** context for one Action it is
resuming, name that Action:

<!-- continuation-request: refresh-handoff -->
```json
{
  "repository": "<repository>",
  "trusted_producers": ["<trusted-producer>"],
  "revision_protocol": true,
  "handoff": {
    "action_identity": "<resumed-action-identity>",
    "context_available": true,
    "reference": "<machine-local-handoff-reference>"
  }
}
```

When the local context is gone, set `"context_available": false` and omit
`reference`. Reconciliation then reports `handoff_context_unavailable` under
Needs attention, which is all it is: a diagnostic. The same is true of
`handoff_action_unavailable` when the named occurrence is no longer in guidance
— report it and use the projection as returned.

A **Handoff reference** never changes an Action's identity, Readiness, order, or
completion, and it never recreates an Action that Reconciliation removed. Do not
attach one when this session is not resuming that exact occurrence.

This step is complete when at most one Handoff was named, for an occurrence this
session is actually resuming.

## 7. Report the status, not your conclusion

The result's `status` is one of three, and each has exactly one honest reading:

| `status` | What to say |
| --- | --- |
| `guidance` | Present the frontier. |
| `waiting` | **Waiting:** no Action is currently derivable, and no Workstream has a terminal outcome. |
| `complete` | **Complete:** every discovered Workstream has an explicit destination-satisfied outcome. |

An empty Action list is `waiting`, never completion. Say Waiting and stop.

Reconciliation only sees Workstreams a Transition owner has adopted. When the
projection is empty, say so plainly — unadopted work is invisible here, and that
is a reason to publish, not a reason to guess.

## 8. Stay read-only

`/next` never publishes, mutates GitHub, repairs the index, ranks independently,
authorizes, or executes.

| Never | Because |
| --- | --- |
| `continuation publish` | Only a Transition owner publishes, from its own durable transition. |
| `continuation repair-index` | It writes. Report the repair diagnostic and leave it. |
| `continuation record-dispatch-result` | Only a Dispatch records evidence. |
| An `automation` block | That is authorization, and this is a refresh. |
| `gh issue close`, label edits, comments, pushes | A question about what to do next may not change what is true. |
| Performing the Action you just reported | Reporting it is the whole job. Hand the Instruction to the user. |
