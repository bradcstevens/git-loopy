# The run-wide default pair is `claude-opus-5 @ xhigh`, and it reserves the ceiling

**Status:** accepted

Implemented by [#401](https://github.com/bradcstevens/git-loopy/issues/401), under the
per-issue routing spec [#400](https://github.com/bradcstevens/git-loopy/issues/400). Decides
[#286](https://github.com/bradcstevens/git-loopy/issues/286). Companion to
[ADR-0035](0035-the-locked-routing-table.md) (the routed pairs) — this one is about the pair an
issue gets when nothing routed it, which today is every issue.

The run-wide default was **three different values at once**: `claude-opus-4.8 @ max` in the
built-in constant of all three Orchestrators, `claude-opus-5 @ high` in this repository's own
tracked Config, and `claude-opus-4.8 @ max` as the Wrapper contract stated it — a statement that
had been true and quietly stopped being the pair the flagship consumer actually ran. It is now
one value, family-wide: **`claude-opus-5 @ xhigh`**.

## The rule that is invisible in every artifact

**The default does not *spend* the ceiling, it *reserves* it.**

`max` is the escalation rung — the single pair a stalled issue is retried at. A default of `max`
means an unclassified issue that fails silently is retried at the **identical** pair, running
the identical computation for no new information, twice, at up to two hours a session. `xhigh`
is one rung below, so the rung becomes reachable and escalation is a real pair change.

`max` is not lost under `xhigh`. It becomes the thing unclassified work escalates *into*, once
it has proved it needs it. Nothing in the code says this, which is why it is here: a future
reader sees a top-of-ladder model held one notch below its ceiling and "corrects" it.

## Why quality, not cheap

The argument for a cheap default is *"unlabelled work is a triage failure, so make it visible
and cheap."* The premise is false. **Unlabelled is not a rare failure, it is the entire
corpus** — nothing in the toolchain produces a `task-type:` label, so a cheap default would not
surface a rare miss, it would silently downgrade every issue git-loopy has ever run.

Two further strikes, both from decisions already made. Escalation is **blind to "produced
garbage"** — progress is commit-shaped, not quality-shaped — so a cheap model that confidently
commits bad work never escalates; for *unclassified* work you know nothing about the task, so
that failure is exactly the silent kind ADR-0035's own rule forbids leaning on. And cheap pays
twice: broad escalation burns a full failed session before retrying at the expensive rung
anyway.

Positively: **unclassified work is planning-shaped.** An issue nobody could classify is one
whose session's first job is working out what it is.

## Why `claude-opus-5`

`gpt-5.6-sol` is out for the reason ADR-0035 gives for `review`, with more force: the default's
Lane is write-capable and handles work nobody classified. `claude-opus-4.8` is out because its
corrected list price is identical to Opus 5, making the move cost-neutral and a strict
capability upgrade — and because it was the only model in the config that **no route pointed
at**, a seventh model maintained solely for the residual.

## The pair is atomic, and that rule is deliberate

Every Orchestrator defaults the *model* unconditionally but the *effort* **only when the model
is also unset**. `GIT_LOOPY_MODEL=claude-opus-5` — the identical model — resolves to *no*
effort, not `xhigh`. That is kept, not collapsed: name a model and you have opted out of the
kit's effort too, and the pair becomes "let the backend pick".

Collapsing it would ship spurious warnings. `gpt-5-mini` accepts only `low | medium | high`, so
an unconditional effort default would give it `xhigh`, hit the dropped-effort branch — which
warns unconditionally — and print a warning about an effort the operator never asked for. It
would also make every existing `GIT_LOOPY_MODEL=<cheap>` run slower and dearer.

## Lockstep, not Python-first

All three Orchestrators change together. The routing and lifecycle work around this decision is
staged Python-first because it is new mechanism on a floor the ports lack. This is the opposite
shape: the default pair **already exists identically in all three**, and it is stated in §11's
precedence-spine table, a MUST for every Orchestrator. Changing one Runner would not defer
parity — it would manufacture divergence in a surface currently in parity, and make the written
contract *false* for the other two rather than merely unimplemented.

## What the default still is

Mostly a **seed**, not a runtime fallback. `init` always writes `model` and `reasoning_effort`,
and the config-file tier does not suppress routing, so for anyone who has run `init` the "global
default" is *their* configured pair. The built-in governs the wizard's default answer, `--yes`,
and the run with no config file in either scope — a state where `[routing]` is also absent, so
the default is not a residual at all: it is the whole policy.

An explicit `--model` / `GIT_LOOPY_MODEL` does **not** reach the built-in default. Suppression
sets the pair to the operator's explicit value; the pair in force is theirs, not the kit's.

## Consequences

- A one-notch downgrade from `max` for the no-config population. Accepted: it buys them a
  second, stronger attempt they do not have today.
- The residual's escalation is **weak** — an effort bump, not the model flip that the evidence
  says rescues stalled work. Weak beats none, and it costs the same session either way.
- The relation to ADR-0035's `planning` row — *the residual runs the planning model and reserves
  the ceiling* — is **rationale, not mechanism**. The default stays an independent constant and
  is never derived from `RECOMMENDED_ROUTING`: a derived default would be undefined exactly
  where the default matters most (no config file, so no `[routing]` table), and would let
  `config routing set planning …` silently move it. If a later decision moves `planning` off
  Opus 5 that sentence becomes false and nothing fails. That is what this record is for.
  [ADR-0048](0048-the-recommended-routing-table-is-retuned.md) has since dropped `planning` to
  `xhigh`, so the row and the default are now the *same pair* rather than a near-miss — and the
  rule that reserves the rung is unchanged, because it was never read off that row.

**Review trigger:** a change to the **escalation rung**, or a roster change touching
`claude-opus-5`.
