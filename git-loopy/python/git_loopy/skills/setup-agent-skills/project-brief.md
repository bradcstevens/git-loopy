# Project Brief Branch

Use this branch only after the user opts in and supplies overview text. Treat that
text as the factual source for both outputs.

## Rewrite the brief

Produce a polished project brief that:

- preserves every substantive fact, constraint, uncertainty, and intent from the
  user's input
- fixes grammar, spelling, capitalization, and punctuation
- removes filler, repetition, throat-clearing, and conversational asides
- uses concise paragraphs and descriptive headings
- splits multi-topic or long input into sections such as purpose, users, goals,
  scope, constraints, or success measures only when the input supports them
- preserves the project's own terminology and does not invent requirements,
  commitments, dates, users, or technical choices

Write the result to `PROJECT_ROOT/BRIEF.md`. Use a single H1 title derived from the
project name or the user's title, then H2 sections as needed. The file contains only
the polished brief, with no editing notes or raw-input appendix.

Treat the user's opt-in and supplied text as authorization to create or replace
`BRIEF.md`.

## Update `README.md`

Derive a concise README overview from the polished `BRIEF.md`, using the same facts
and vocabulary. Include one to three short paragraphs, add a compact goal list only
when it improves scanning, and finish with:

```markdown
[Read the full project brief](BRIEF.md).
```

Wrap the overview in these markers:

```markdown
<!-- setup-agent-skills:overview:start -->
## Overview

<overview>

[Read the full project brief](BRIEF.md).
<!-- setup-agent-skills:overview:end -->
```

If the markers already exist, replace exactly that block. Otherwise insert the
block immediately after the first H1 heading; when the README has no H1, prepend a
project-name H1 and the block. Preserve all README content outside the managed
block.

## Completion criterion

The branch is complete when:

- every substantive input fact appears in `BRIEF.md` or was collapsed only because
  it duplicated another statement
- `README.md` contains exactly one managed overview block
- the README overview and `BRIEF.md` agree on all claims
- the rest of `README.md` remains intact
