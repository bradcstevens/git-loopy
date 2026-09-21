# JEV Breakdown: The First AI Model Built For Code

https://www.youtube.com/watch?v=2Bs0Ink_-Uo

**Rob Shocks · 10:36 · 51 frames + native captions**

## The pitch
Jev is a new model from **TypeSafe AI** that launched the day before filming (the frame shows a SiliconANGLE headline: *"TypeSafe AI exits stealth with $40M to build AI for use by software"*, ~Sept 15 2026). Rob credits co-founder **Diogo Almeida**, whom he describes as an early ChatGPT co-founder — that's his claim, worth verifying.

**The core inversion (00:00–01:10):** every frontier model produces *text for humans to read*. Jev produces **decisions for code to act on**. It literally cannot write a sentence or explain itself. The launch page frames it as "The First (Public) System One Model" — a Kahneman reference, where every LLM to date has been trying to be System 2 (slow, deliberate, one token at a time).

**How it works (05:43–06:00):** no token generation, no autoregressive loop. You send a *state* (text/JSON) plus a set of typed questions; it evaluates **all of them in parallel** and returns typed JSON with calibrated confidence. Trained via "reinforcement learning with calibrated decisions" rather than RLHF for human preference — Rob's argument being that human-preference training causes mode dropping, overconfidence, and unreliability for machine consumption (00:37).

## Three question types (03:26)
| Type | Does | Example output |
|---|---|---|
| **Choice** | pick one option | billing / **technical** / sales → 0.84 |
| **Score** | place on a scale | calm ← civil → angry → 1.4 |
| **Noul** | yes/no probability | "is this urgent?" → 0.99 |

Rob openly jokes about not knowing how to pronounce "Noul" (04:02).

## Claimed numbers
- **20–200x faster**, **40–400x cheaper** with output tokens free (00:41 tweet)
- ~0.1s latency, **$42 / billion tokens** ("Decisions For Code" slide, 00:55)
- Side-by-side demo: TypeSafe **$0.000081 / 0.134s** vs LLM **$0.013880 / 8.566s** (01:56)
- **Vercel** — Guillermo Rauch says it's now default mode in v1 as a safety reviewer on every command, *"up to 18x faster (p95) and more accurate,"* coming to Vercel AI Gateway. They replaced Gemini 2.5 Flash Lite and it saturated the eval (02:02–02:37)

## Ten Uses For Jev slide (01:37)
Agent Guardrail · Writing Linter · Model Router · RAG Filter · Ticket Triage · Citation Checker · Skill Picker · Semantic Search · Corpus Map-Reduce · Live UI

## Demos shown
- **Doom** — real-time play, ~$7 for an hour of calls (05:00)
- **Wikipedia Speedrun** — DNA → Manipuri pony in **1.7 seconds** (06:19–06:34)
- **Smart home** — natural language → device state in **185ms** (06:40–07:10)
- StarCraft mission-winning, and OpenCode browser-use (both "blazingly fast")
- **Playground walkthrough** (08:06–09:38) at `console.typesafe.ai`: sky-color choice (81% baby blue, 78% confidence), then a help-desk triage combining priority choice, routing choice, deadline noul (5%), and a **tool-call choice** → `ticket_status`

## Why he thinks it matters
The framing at 04:20: traditional software is deterministic (if/then/else), agents are non-deterministic and drift. Jev slots into the hybrid middle — "AI-powered software" — handling decisions you couldn't previously make deterministically. He's most excited about **MCP tool selection and skill routing** (07:56): rapidly picking the right tool from a large list without burning a full LLM call.

**His closing ask (09:48):** look at the big decision flows in your system that cost real time and money — can they collapse into a structured yes/no input/output that's far faster and cheaper?

## Practical access
Waitlist at typesafe.ai — he got in within a couple of hours. Playground, usage dashboard, API keys, and it can be installed directly into Claude Code.

Worth noting: this is a day-one enthusiast breakdown, not an independent evaluation. All the speed/cost figures come from TypeSafe's own materials or vendor tweets.