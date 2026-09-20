# The Dashboard is pointer-navigable, and an Agent's facts survive a Collapse

**Status:** accepted

The **Dashboard** already captures the mouse, and hit-tests exactly one target: the **Activity**
band's handle. Every other gesture is received and discarded — a click on a **Queue** row does
nothing, and the scroll wheel is explicitly ignored. Drilling into an issue is keyboard-only.

## A single click drills in, because the core has no clock

A click on a Queue row selects that row and opens its **Log**, and the wheel scrolls whichever band
the pointer is over.

Single click rather than double, for a reason worth recording so nobody restores the gesture by
reflex: detecting a double click requires the elapsed time between two events, and the Rust
**Dashboard** core is deliberately pure — its library-purity test forbids it from reading a host
clock — so the gesture would cost an injected clock existing only to time it. Drilling in is
reversible with the Back key, so a stray click costs a keypress rather than a state change, which is
what makes the cheaper gesture the safe one. The wheel changes no state at all.

This keeps the degradation order the **Collapsed** band already established — drag, then click, then
keys — so a terminal that reports less than motion still has every action available.

## The Agent's facts stay visible when the band is Collapsed

**Task type**, **Routed pair** and **Context fill** are resolved at **Pickup** and published, and
then reach an operator almost nowhere: the pair renders only in the Queue's Route column, which is
the first column a narrow terminal surrenders and needs roughly 108 columns to appear at all, and
the Task type renders nowhere.

[ADR-0021](0021-activity-windows-per-agent.md) and
[ADR-0022](0022-per-agent-insight-facts.md) already decide that their home is the **Activity
window** header. What this adds is that the **Active issue**'s pair survives a **Collapse**: a band
an operator collapsed to see more **Queue** should not take the Run's current configuration away
with it.
