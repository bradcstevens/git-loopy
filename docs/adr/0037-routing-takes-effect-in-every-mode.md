# Routing takes effect in every mode, not only in Parallel mode

**Status:** accepted

Implemented by [#404](https://github.com/bradcstevens/git-loopy/issues/404), under the per-issue
routing spec [#400](https://github.com/bradcstevens/git-loopy/issues/400). **Reverses**
[ADR-0027](0027-routing-is-calibrated-by-measurement.md)'s amendment *"Calibration only affects
Parallel mode"*, and with it the scope [ADR-0028](0028-measured-routing-is-a-committed-tier.md)
and [ADR-0029](0029-agents-infer-the-task-type.md) inherited from it.

A **Routed pair** now takes effect wherever it is resolved: a serial **Iteration** runs on the
pair its own **Pickup** resolved, exactly as a **Lane** does.

## The premise died two slices before the rule did

ADR-0027 scoped routing to **Parallel mode** on a fact about the serial loop, not on a judgement
about routing: *"the serial path folds the entire pool into a single prompt and runs it on the
run-wide default. One session, many issues, many task types, one model — there is no per-issue
thing to route."* That was true, and it made routing in serial **incoherent** rather than merely
unimplemented — you cannot pick one pair for a session working seven issues of five task types.

[ADR-0032](0032-the-runner-picks-the-oldest-eligible-issue.md) gave a serial Iteration a
**Pickup** of its own, and [#394](https://github.com/bradcstevens/git-loopy/issues/394) made that
Pickup bind exactly one **Active issue** and resolve its pair there. The incoherence was gone.
What remained was a predicate — `routing_scope.routing_in_force` — that took a pair the runner
had *already resolved from the issue's labels* and threw it away in favour of the run-wide one.

That left the worst arrangement available, and precisely the one ADR-0027 exists to forbid:
`git-loopy` with no flags is serial, so the **default invocation** was the one invocation where
an operator could configure a `[routing]` table, watch the runner parse it, resolve through it,
refuse a mislabelled candidate on the strength of it — and get the same model as before.

## Why this was not folded into #394

#394 recorded the falsification in the module itself and deliberately did not flip the rule, for
one reason: the flip is a **reversal of a decision**, not a consequence of moving the pickup.
Three surfaces told operators the scope was real — `config get` reported the measured tier
*inert*, `git-loopy calibrate` **refused to spend** in serial, and the Python README documented
both — and a reversal that arrived as a side effect of a pickup refactor would have changed all
three without anything saying why. It also owed the calibration question its own answer, which
is the section below.

## A Calibration and a Demotion become meaningful in serial

The refusal was correct on its own terms: a **Calibration** that measured a pair nothing would
apply would spend **AI Credits** for no change, and hours of an operator's time for a committed
artifact with no effect. That reasoning is now inverted at its premise — the pair *is* applied —
so the refusal is deleted rather than softened. Refusing to measure what the default mode now
runs on would be the same silence in the opposite direction.

**Demotion is meaningful but still empty in serial**, and that is now an honest gap rather than a
scope. A **Demotion** reads finalized **Lane contributions**, and a serial Run opens none — the
`Contribution` record is a Lane's, by definition. So a serial Run has a pair worth measuring and
no row to measure it on. Building that row is a separate slice; recording the gap here is what
stops the next reader from reading `finalized_contributions == ()` as a surviving piece of
ADR-0027's scope.

## What the family owes

Routing is Python-first by design: neither native Orchestrator implements §14 at all, so neither
has a pair to apply or to discard, and this reversal changes nothing for them. That divergence is
now **stated** in the Wrapper contract as a recorded decision rather than left as an omission —
a native port that implements routing acquires this rule with it, and must not reproduce the
Parallel-only scope on the way.

## Consequences

- `routing_scope.routing_in_force` answers `True` at every parallelism a Run can have. The module
  keeps its one-rule-one-place role — the **Demotion** path, the roster preflight and the price
  staircase they share all still ask it — so a future narrowing has exactly one place to land.
- `calibration_refusal` and the `SERIAL_INERT_NOTE` are **deleted**, not left returning nothing:
  a constant holding a false statement is a constant a surface can print.
- `config get`, `config list` and `config routing list` print the winning tier with nothing
  qualifying it, because there is no longer an exception to declare.
- The roster-drift preflight and its re-calibration hint now reach serial operators, who can act
  on them.
- The Wrapper contract bumps to 1.20 and states both the reversal and the native divergence.
