# "Jev is incredible" — Theo (t3․gg), 30:31, uploaded today

https://www.youtube.com/watch?v=F3YXg7AaKWE 

Transcript-only pass (native YouTube captions, no frames). Names below are as auto-captioned, so a few are mangled.

## The thesis
Jev is a new model from **Typesafe AI** — and it is *not* another LLM. It doesn't generate prose or code. It takes unstructured state in and returns **typed, probabilistic JSON decisions** out. Theo's framing, repeated relentlessly: it's a **"System 1" model** (Kahneman's fast/intuitive thinking) versus the System 2 reasoning models everyone else ships. Named after **Jevons paradox** — make a thing cheap enough and consumption explodes.

Built by an ex-OpenAI researcher who worked on RLHF and the research behind ChatGPT, after two years in stealth. Invite-only early access, reachable via OpenRouter and the Vercel AI gateway.

## Why it matters (04:07–11:32)
- **Speed:** 70–500 ms vs 3–300 s for equivalent LLM classification — 40–200× faster.
- **Cost:** ~$0.04 per million input tokens vs ~$10 on a frontier model. **Output tokens are free** ("too cheap to meter").
- **Zero structured-output errors.** 0% format-error rate. By contrast Haiku was measured at a 45.5% error rate — Theo: "that model is so bad and it needs to stop being used for anything."
- **Calibrated confidence** on every output, so you can threshold instead of guessing.
- **Constraint:** 32k context, no vision (yet).

## Demos shown
| Time | Demo |
|---|---|
| 05:02 | Checkers — instant responses, but Theo crushes it while barely paying attention |
| 17:36 | Doom via game-state JSON, 10 decisions/sec, <$7/hr — spins wildly because each frame wipes its "memory" |
| 18:59 | Wiki racing from raw HTML links |
| 20:27 | Ryan classifying 1,500 emails — 200 ms avg, 38/sec |
| 21:52 | Booking a flight in 7.1 seconds |
| 26:30 | Theo's own 1,118 Claude Code threads / 32,311 messages classified for $37 — found ~half his work was bug fixing, ~20% PR review |

He also spends 07:52–09:14 on **BAML** as the prior art: a DSL that forces and repairs structured output from normal LLMs, which Jev makes unnecessary for this class of task.

## The rant (22:20–26:02)
Roughly a third of the video is Theo pushing back on bad takes:
- **Don't use it as an LLM judge.** Braintrust tweeted this; he says it makes him distrust them. A model that can't reason can't score reasoning.
- **Don't use it for context compaction.** His longest argument: compaction is *synthesis*, not filtering; Jev can't see reasoning traces (labs don't return them); 32k context isn't enough; editing history invalidates the prefix cache; and models are RL-tuned on their own compaction behavior. "People have actually tried this and benched it. It doesn't perform well at all."

## The takeaway (28:22–end)
> "How many seconds would it take you to answer once you've perceived the information? Under 10 seconds → good fit. Over 10 → not."

Treat it as **a library you install or a function you call**, not a model you chat with — "use this like an `if` statement." Sponsor segment (Depot CI) runs 00:56–02:45 if you want to skip it.