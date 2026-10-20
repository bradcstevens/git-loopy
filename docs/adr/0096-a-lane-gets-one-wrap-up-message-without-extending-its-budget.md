# A Lane gets one wrap-up message without extending its budget

A Lane brief states its session budget and deadline in local time with a numeric
UTC offset (ADR-0058). Before the unchanged send timeout, the Runner sends the
still-live session one immediate steering message asking the Agent to commit
coherent work as a normal agent commit, leave the rest uncommitted for the
Checkpoint, and record on the issue what is done and what remains. This favors a
useful handoff over discovering unfinished work only after timeout, without
extending the bounded attempts of ADR-0040.

The lead defaults to the smaller of 15 minutes and a tenth of the actual session
budget. Config can override it, with environment over project over global; a
nonpositive, nonfinite, or at-least-budget lead is refused. Local and GitHub
Actions Lanes use the same behavior, with the remote host checking against its
capped session budget and rendering the deadline when its session actually
starts, not when the job is dispatched.

The original SDK wait owns completion and timeout. Its timer is never restarted;
early completion cancels the warning, a late timer sends nothing, and warning
delivery failure is logged without retry or an extension. The warning cannot
guarantee the Agent finishes its handoff, and does not publish, close, or authorize
otherwise forbidden work.

This is not the Continuation retired by ADR-0046: it is one transient message in
an already-running session, not a durable typed record, successor selection,
reconciliation mechanism, or a new attempt. The requested issue comment is plain
evidence, the discipline that ADR-0046 explicitly retained.
