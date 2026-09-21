## Jev is the FIRST of a Whole New Class of AI Models (Here's How to Actually Use It)

https://www.youtube.com/watch?v=bA8WeHYmJko

**Cole Medin** · 17:13 · uploaded today

### The thesis
Jev (by **TypeSafe AI**) isn't an LLM — it's the first public **"System One" model**. It generates no text at all. You give it a *situation* plus a set of *typed multiple-choice questions*; it returns *decisions with calibrated confidence scores* in ~200ms for a fraction of a cent. Trained with a new algorithm, **RLCD** (Reinforcement Learning for Calibrated Decisions), as opposed to RLHF `[01:58–02:16]`.

The landing page (visible throughout) is blunt about tradeoffs: **not good at System 2 tasks, not trained on specialized domains, not a generative chat model** — but claims *20–200× faster*, *40–1,000× cheaper*, 100% structured-output validity, and "frontier-level intelligence for System 1 tasks."

### The numbers he shows
- **Accuracy-vs-cost scatter** `[04:48]`: Jev sits alone at the far-left of the frontier (~67% accuracy at ~$0.0002/workflow) while opus 5 / sol / terra cluster at 10–100× the cost.
- **Price per million tokens** `[05:28]`: Jev **$0.042 in** vs GPT-6 Astra $10/$50, Claude Fable 5.1 $10/$50, Claude Sonnet 5 $2/$10, GPT-5.6 Luna $0.20.

### Live demos
| Time | Demo |
|---|---|
| `03:33` | **Customer support triage** — raw JSON in/out in a terminal. Stripe complaint → routes to `billing` at 64% confidence, frustration score 1 ("frustrated but civil"). Cost: **$0.000019**. |
| `05:48` | **Playing his own indie game** on localhost at ~3 decisions/sec — live confidence bars for attack/move/dodge on the left, hands off the keyboard. LLMs can't do this: too slow, too expensive. |
| `09:08` | **Archon PR-triage workflow** — `classify → route → light-review / deep-review → report`. Jev handles the first two (pure decisions), an LLM does the actual review. |
| `11:48` | **LLM router** — 200 requests, 219ms median Jev overhead, routing to strong/coding/open/fast tiers. All that routing cost **0.4¢ total**, averaging 0.2s per decision. |

### Community examples `[12:22–14:15]`
Jev playing **Doom** (posted on X), the **browser-use/jev-ultrafast** repo — browser automation without an LLM in the loop, since "click this button" is just multiple choice — a **Pong** comparison where Jev keeps up at human rate while Gemini 3.8 Flash and Claude Haiku 4.5 need the game slowed way down, and the **awesome-jev** curated repo (classification & routing 17 entries, agent decisions 21, guardrails 16, etc.).

### The criticism, addressed `[14:52–16:26]`
The common pushback: *"this is just a glorified classification model — we've had those for decades."* Cole partly concedes, then argues the difference is **generality**. His TensorFlow/PyTorch classifiers only ever did one task with one dataset; Jev takes any situation. He demos the silliest possible case in the TypeSafe playground — *"Which country has the coolest buildings?"* → **Germany, 63% confidence**, and it says Germany every single run.

### Practical takeaway
Don't swap LLMs for Jev. Drop it into the **decision points** — the gates that used to be a regex or a slow LLM call: routing, classification, guardrails, verification. Access via **OpenRouter** (or typesafe.ai directly; waitlist cleared in a day). Sponsor segment for Firecrawl runs `06:57–08:52`. Not sponsored by TypeSafe.

---
Work dir left at `/var/folders/.../watch-scemq1yl` (46MB) in case you want to dig into a specific section — say the word and I'll clean it up or zoom in.