# `git-loopy-tui` — the Rust semantic Dashboard core

One Cargo package with two targets:

- an **embeddable library** (`git_loopy_tui`) that turns the shared git-loopy
  Event stream into the renderer-neutral semantic Dashboard view, and
- a **thin binary** (`git-loopy-tui`) that is nothing but transport around that
  library.

The split matters because the same core has two consumers (ADR-0013): the shell
and PowerShell Orchestrators launch the standalone binary and feed it Events,
while the future Rust Orchestrator embeds the library in-process. Neither may
get its own behavior, so every semantic decision lives in the library and the
binary owns only argument parsing, stdin, and stdout.

## What the library does

```rust
use git_loopy_tui::{
    project_run_view, DashboardState, Event, IssueRef, RunInputs,
    TerminalCapabilities, Timestamp, ViewContext, Zone,
};

let mut state = DashboardState::new(RunInputs {
    model: Some("gpt-5.6-sol".into()),
    reasoning_effort: Some("high".into()),
});
for line in trace.lines() {
    if let Some(event) = Event::from_jsonl_line(line) {
        state.apply(&event);
    }
}

let view = project_run_view(
    &state,
    &ViewContext {
        now: Timestamp::parse_rfc3339("2026-05-16T00:00:05Z").unwrap(),
        zone: Zone::from_offset_minutes(-360),
        capabilities: TerminalCapabilities::default(),
    },
    &IssueRef::number(42),
);
```

`DashboardState` is a pure reducer over decoded Events; `project_run_view` is a
pure projection. Everything ambient is a parameter:

- **the instant** — `ViewContext::now`, never the host clock, so a fixture
  snapshot stays reproducible forever;
- **the zone** — a fixed offset, never the host's `TZ`, so it only ever moves
  how instants are *rendered*, not what was measured;
- **terminal capabilities** — injected for a renderer to consult and
  semantically inert; two projections of one state are byte-identical no matter
  what the terminal can do.

The library boundary contains no shell, PowerShell, child-process, stdin, or
filesystem assumption. `tests/library_purity.rs` reads the library's own sources
and fails if a new module reintroduces one.

## What the binary does

```
git-loopy-tui [--render-at INSTANT] [--utc-offset-minutes N] [--issue REF] \
              [--model NAME] [--reasoning-effort LEVEL] < events.jsonl
```

Reads a JSONL Event trace, writes the projected semantic view as JSON. An
unreadable line is skipped rather than fatal — unusable telemetry never blocks a
render — and malformed usage exits `2`, matching the family's locked CLI
framing. Terminal rendering is not this target's job.

## Conformance

The suite's oracle is the shared, language-neutral fixture
[`../conformance/dashboard-insights.json`](../conformance/dashboard-insights.json)
— the *same* file Python's `test_conformance.py` drives. Neither member is the
other's oracle, so the two cannot drift toward each other:

| Test | Pins |
| --- | --- |
| `tests/dashboard_conformance.rs` | Every fixture case × snapshot: band inventory and order, Queue columns and ordering, scopes, placeholders, unavailable measurements, Iteration history, drill-in |
| `tests/injected_environment.rs` | Capabilities are inert; the zone moves only rendering; elapsed comes from the injected instant |
| `tests/additive_compatibility.rs` | An unmodelled Event type and unknown fields still reduce to the same view |
| `tests/library_purity.rs` | The library reaches for nothing the caller did not supply |
| `tests/binary_seam.rs` | The binary is a thin shell over the library, through the real process boundary |

```bash
cargo test --manifest-path git-loopy/tui/Cargo.toml
```

CI runs this, `cargo fmt --check`, and `cargo clippy -- -D warnings` in the
Runner family gate (`.github/workflows/runner-family-gate.yml`).

## Dependency floor

`serde` and `serde_json` only. The instant and calendar arithmetic in
`src/timestamp.rs` is hand-rolled to keep the dependency surface small for the
cross-compiled release artifacts, and it byte-matches Python's
`datetime.isoformat()` because the fixture's expected values are that format.
`serde_json`'s `preserve_order` feature is load-bearing: it makes the projected
band and column order observable, which the fixture pins.
