# A Conformance distribution is obliged by a stream, not by serializing it

**Status:** accepted

Decided by [#432](https://github.com/bradcstevens/git-loopy/issues/432).

`distributions` on a Conformance stream case has meant "this member round-trips these bytes through
its production serializer." It now means "this member is obliged by this stream, in the way its role
permits" — so the Rust **Dashboard** core can be listed on
`rolling_stream_cases` and assert that it *folds* the stream, where an **Orchestrator** asserts that
it *writes* it.

## Why the old meaning could not admit a consumer

The axis was built for producers, and says so. The **Wrapper contract** requires that *"every family
member drives those streams through its own production serializer, including the members that
schedule no Lane: an Orchestrator that cannot produce a rolling record must still read and write the
same bytes"* (`docs/wrapper-contract.md:845`). That is why `shell` and `powershell` appear on
rolling cases while declaring `rolling_dispatch: false`, and the suites assert exactly that:
`shell/tests/test-event-conformance.sh:180` selects `.distributions | index("shell")`, drives
`git_loopy_to_jsonl_line`, and compares bytes against the pinned `jsonl` string. PowerShell and
Python do the same.

The Rust core cannot make that assertion, and not by omission. `Event` derives `Clone, Debug` only —
not `Serialize` — and there is no `to_jsonl` anywhere in `git-loopy/tui/src/`; the sole
`serde_json::to_string*` call (`tui/src/main.rs:235`) writes the semantic *view* model. Honouring
the old meaning literally would mean making `Event` serializable and writing an Event emitter into a
consumer that will never emit an Event, then gating it forever. That is dead code with a permanent
gate attached.

Before this decision no fixture in `git-loopy/conformance/` carried a `rust` or `tui` distribution
at all.

## What each role now asserts

- **An Orchestrator** asserts the unchanged obligation: its production serializer reproduces the
  pinned bytes, whether or not it can schedule a **Lane**.
- **The Dashboard core** asserts that it folds the stream without diagnostics and produces the
  pinned view. Types it does not model still degrade to `EventPayload::Other`; they do not become
  unreadable lines.

The second half is the load-bearing limit. A rolling stream case contains every declared rolling
type, including the nine no producer emits, so requiring *every* type in a pinned stream to reach a
modelled payload would force the core to reduce Events that cannot occur and would contradict the
additive tolerance `tui/tests/run_loop.rs:253` exists to protect. Fold-to-pinned-view obliges the
consumer for the whole stream without obliging it to model every literal in the stream.

## Considered and rejected

- **Build a real Rust Event serializer** so it round-trips like every other member. Honest and
  symmetrical, and it would buy a real property — proof that decode is lossless. Rejected because
  the cost is a permanent emitter inside a consumer, maintained solely to satisfy a fixture axis.
- **Leave the Rust core off the axis** and pin its reduction only in
  `dashboard-insights.json`. Cheapest, and it keeps one axis per question. Rejected because the
  rolling streams are where the rolling vocabulary is pinned in full, and a consumer absent from
  them is a consumer nothing obliges to keep up as they grow.

## Consequences

- **The three Orchestrator suites are unaffected.** Their selector and their byte comparison stay
  exactly as they are; only the axis's definition widens beneath them.
- **`rolling_stream_cases` and `dashboard-insights.json` keep different jobs.** The first pins
  emission, wire form, and now consumer fold; the second remains *"the semantic boundary between
  Orchestrators and live-interface implementations"* and is where the expected Dashboard model is
  pinned. [ADR-0044](0044-the-dashboard-core-attributes-rolling-work-by-the-contribution-triple.md)
  adds rolling cases there.
- **A future non-Orchestrator consumer has a place to be listed.** Any replay or export tool obliged
  by a stream can now join the axis without pretending to emit.
- **The wording in `docs/wrapper-contract.md` must change with it.** The sentence quoted above
  describes only the producer half now, and a reader who finds `rust` on a case while reading that
  sentence would reasonably conclude the core emits Events.
