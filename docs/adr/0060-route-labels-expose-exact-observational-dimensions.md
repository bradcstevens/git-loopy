# Route labels expose exact observational dimensions

**Status:** accepted. Python serial and Lane publication write exact `model_id:`, `model_context:`, and `model_effort:` labels and remove a legacy combined association on the issue they touch. `git-loopy route-labels migrate` reconstructs those dimensions from a trustworthy local record or a matching historical projection, removes leftover legacy associations, and deletes an unused legacy definition only after issue and pull-request checks. A later capacity-only refresh is not implemented. Shell and PowerShell routing remain deferred under ADR-0057. This is not final Dynamic-default activation.

A human-led `/grill-with-docs` session chose separate, readable Route labels instead
of the truncated combined label and identity suffix. This supersedes only the
single-label representation in ADR-0057: labels remain observational output, not
routing inputs or per-issue pins. Making them authoritative was considered and
rejected because a previously published dynamic choice would otherwise become an
implicit pin on subsequent work.

## Exact dimensions, not an encoded identity

The Runner owns `model_id:`, `model_context:`, and `model_effort:`. Publication
replaces stale or conflicting labels in those namespaces, keeps at most one label
per dimension, and preserves unrelated labels. Model IDs retain their exact
spelling, including dots; effort retains the exact supported value.

Context means verified full capacity for the selected model and Context tier,
not usage, remaining tokens, a compaction budget, or the model's largest
advertised window. Use exact decimal K/M units: `200K` is 200,000 tokens, `1M`
is 1,000,000 tokens, and 1,048,576 tokens is `1.048576M`. Do not round.
Below one million tokens use K; at or above one million use M, with only the
fractional digits needed to preserve the exact count.

Omit `model_effort` when effort is not configurable; the supported value `none`
is distinct and produces `model_effort:none`. Omit any dimension whose exact
value cannot be verified or represented within GitHub's label limit, remove its
stale value, and visibly report incomplete projection. An inapplicable effort
is not an error. Never truncate, hash, fabricate, or silently substitute a value.
Publication failures alone do not block otherwise valid work.

Capacity verified later by the authenticated work harness updates the current
assignment's label without rerouting or repeating the routing-decision comment.
An observation belonging to an older assignment must not overwrite newer labels.
Capacity belongs to that exact model/tier assignment, not a guessed model roster.

## Preserve the output boundary

Canonical local routing records remain authoritative. Preserve durable pending
delivery, bounded retries, idempotence, visible partial failure, and stale-delivery
protection. Exhausted delivery stays failed rather than acquiring a fresh retry
budget on every Run. Exclude both old and new Route labels from routing task inputs
and reuse comparisons so publication cannot invalidate its own decision.

Historical comments and internal identity hashes remain intact for provenance
and duplicate prevention. No identity suffix appears in a new label name.
Upgraded publication converts pending legacy deliveries instead of replaying
their old label spelling.

## Explicit historical migration

Provide an explicit, repeatable migration scoped to one operator-selected
repository, covering open and closed issues. Reconstruct new dimensions only
from trustworthy final routing records or matching historical projections;
never infer an exact value from truncated legacy label text, assume historical
capacity from a current model listing, or buy new model selection for cleanup.
Remove legacy `git-loopy-route:*` issue associations even when some dimensions
cannot be reconstructed, and report what remains unknown.

Delete legacy repository label definitions only after verifying they are unused,
including checking pull requests because definitions are shared. Do not remove
unrelated labels or rewrite historical comments. Normal publication also removes
legacy associations from every issue it touches; it does not sweep all issues on
every Run.

Operators must stop or upgrade every publishing Runner for that repository before
migration. Detectable local conflicts can be refused, but a local command cannot
certify the absence of old publishers on other machines. The promise that legacy
labels do not return requires this coordinated cutover; deletion alone cannot
prevent an older Runner from recreating them.

## Delivery boundary

Implement through Python's existing serial and parallel publication paths and
document the Runner-family contract. Shell and PowerShell routing support remains
explicitly deferred under ADR-0057; this decision does not claim it exists.
Prefer the existing publisher/tracker test boundary, command-level migration
coverage, and focused serial/parallel and route-reuse regressions.
