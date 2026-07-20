---
name: next
description: determine the next step in a session per the general skill workflow, given the current state of the conversation and last skill invoked. Recommend the next skill to invoke and the prompt to use for that skill.
---

# General Skill Workflow

1. `/grill-with-docs` or `/wayfinder`
2. `/to-spec`
3. `/to-tickets`
4. `/implement`
5. `/code-review`

# Recommend, Don't Do

Reflect on the session so far and determine the next step in the conversation, given the current state of the conversation. Specifically recommend the next skill to invoke and the prompt to use for that skill.

If there are multiple possible next steps, provide a ranked list of the options with their respective skill and prompt, with the most relevant option first.