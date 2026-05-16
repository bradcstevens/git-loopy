# `<PROJECT_NAME>`

> **Template notice.** This file is a starting point. `CONTEXT.md` is the canonical domain glossary for a project — the place where ambiguous words get one agreed-upon meaning. It is normally created lazily by the `/grill-with-docs` skill the first time a term needs resolving; this stub exists so new projects start with the right shape. Replace every `` `<PLACEHOLDER>` `` with real content, address every `> 📝` note, then delete this notice. Once filled in, this file is referenced by `AGENTS.md`, by every PRD, and by every slice issue — keep it tight and current.
>
> Full format reference: `~/.copilot/skills/grill-with-docs/CONTEXT-FORMAT.md` (or the project-local copy at `.copilot/skills/grill-with-docs/CONTEXT-FORMAT.md` if `ralph/` was copied in).

## How to use this template

1. Replace every backticked placeholder like `` `<PROJECT_NAME>` `` with real content. Grep for `<[A-Z_]` to find them.
2. Read every `> 📝` blockquote note and act on it, then delete the note.
3. Delete any section whose first line is `> 🗑️ DELETE IF NOT APPLICABLE` that you don't need.
4. If your repo is multi-context (multiple bounded contexts in one repo), delete this file and create a `CONTEXT-MAP.md` at the repo root instead — see the **Multi-context repos** section below for the shape.
5. Run `/grill-with-docs` to extend this file organically as new terms come up; do not pre-populate every term you can think of.
6. Delete this whole **How to use this template** section.

---

> 📝 Replace this paragraph with one or two sentences describing what this context is and why it exists. For a single-context repo this is just the project's domain summary. For a sub-context inside a multi-context repo (`src/<bounded-context>/CONTEXT.md`), describe the boundary this context owns.

`<ONE_OR_TWO_SENTENCE_CONTEXT_DESCRIPTION>`.

## Language

> 📝 The canonical terms in this project's vocabulary, each defined in one tight sentence. Be opinionated — when multiple words exist for the same concept, pick the winner and list the losers under `_Avoid_`. Only include terms specific to this context; general programming concepts (timeout, retry, error) don't belong even if the project uses them heavily. Group under subheadings (e.g. `### Domain entities`, `### Lifecycle states`) only when natural clusters emerge — a flat list is fine for small projects.

**`<TERM_1>`**:
`<ONE_SENTENCE_DEFINITION — what it IS, not what it does>`.
_Avoid_: `<ALIAS_1>`, `<ALIAS_2>`

**`<TERM_2>`**:
`<ONE_SENTENCE_DEFINITION>`.
_Avoid_: `<ALIAS_1>`

**`<TERM_3>`**:
`<ONE_SENTENCE_DEFINITION>`.

## Relationships

> 📝 How the terms above connect — express cardinality where it matters. Two or three bullets is usually enough; this section is a sanity check on the **Language** section, not an ERD.

- A **`<TERM_1>`** has many **`<TERM_2>`s**
- A **`<TERM_2>`** belongs to exactly one **`<TERM_3>`**

## Example dialogue

> 🗑️ DELETE IF NOT APPLICABLE — keep this section once you have real terms; a short dev ↔ domain-expert exchange is the fastest way to show how the terms interact and to expose hidden ambiguity.

> **Dev:** "When a `<TERM_1>` is `<VERB>`d, does it produce a `<TERM_2>` immediately?"
> **Domain expert:** "No — a `<TERM_2>` is only produced once `<PRECONDITION>` holds."

## Flagged ambiguities

> 📝 Words that were previously overloaded or used inconsistently, with the resolution that this file enforces. New ambiguities get appended here as `/grill-with-docs` resolves them. Empty is fine on day one.

- `<AMBIGUOUS_WORD>` was used to mean both `<MEANING_A>` and `<MEANING_B>` — resolved: `<MEANING_A>` is **`<CANONICAL_TERM>`**; `<MEANING_B>` is no longer used.

---

## Multi-context repos

> 🗑️ DELETE IF NOT APPLICABLE — keep only if your repo has more than one bounded context. In that case **delete this `CONTEXT.md`** and create a `CONTEXT-MAP.md` at the repo root with a per-context `CONTEXT.md` under each context folder. Shape:
>
> ```md
> # Context Map
>
> ## Contexts
>
> - [<Context A>](./src/<context-a>/CONTEXT.md) — `<one-line summary>`
> - [<Context B>](./src/<context-b>/CONTEXT.md) — `<one-line summary>`
>
> ## Relationships
>
> - **<Context A> → <Context B>**: `<event or shared-type relationship>`
> ```
