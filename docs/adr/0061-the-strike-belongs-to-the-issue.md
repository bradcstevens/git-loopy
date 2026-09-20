# The Strike belongs to the issue, and the Run's guard counts consecutive abandonment

**Status:** accepted — amends [ADR-0041](0041-the-strike-counts-issues-given-up-on.md)

ADR-0041 made the charge per issue — exactly one **Strike** at the moment an issue is abandoned —
but left the ceiling run-wide, so three abandoned issues end a **Run** under `stuck` even when
twenty workable issues remain. That is the same complaint ADR-0041 raised against its own
predecessor, one level up: a budget for failure that stops work which could still succeed.

## A Strike is accounting, and ends nothing by itself

The Strike becomes purely per-issue. It records that this Run gave up on this issue, and no
accumulation of Strikes terminates anything.

## The Run keeps a guard, and it is not a Strike count

The **Iteration cap** defaults to unlimited, so an unattended Run on a systematically broken
repository would otherwise have no default backstop at all — it would attempt every issue in the
**Pool** before ending under `all_skipped`. Bounded, but far more expensive than today.

So a guard survives, deliberately separate and deliberately not spelled as a failure ceiling: it
counts **consecutive** abandonments and resets whenever any issue reaches **Closed** or
**advanced**. The default stays three, so a broken Run stops exactly as fast as it does now, while a
productive one never trips it.

This is the amendment to ADR-0041, which rejected a resetting counter on the grounds that one commit
between two stalls is a Run grinding rather than recovering. That argument holds for a single
issue's own attempts, where the lifecycle it counts is monotonic, and does not carry here: a
*different* issue closing is direct evidence the Run is productive, which is the only property the
guard exists to test.

## The two Orchestrators without a Pickup are unchanged

They keep counting consecutive unproductive **Iteration**s, exactly as
`conformance/progress-strikes.json` already forks: per-issue accounting is charged from the
**Attempt lifecycle**, and a Runner with no **Pickup** has no lifecycle to charge from.
