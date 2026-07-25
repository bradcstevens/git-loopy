# `git-loopy-tui` — the Rust Dashboard

One Cargo package with two targets:

- an **embeddable library** (`git_loopy_tui`) that turns the shared git-loopy
  Event stream into the renderer-neutral semantic Dashboard view *and* draws
  that view, and
- a **thin binary** (`git-loopy-tui`) that is nothing but transport around that
  library.

The split matters because the same core has two consumers (ADR-0013): the shell
and PowerShell Orchestrators launch the standalone binary and feed it Events,
while the future Rust Orchestrator embeds the library in-process. Neither may
get its own behavior, so every semantic *and presentational* decision lives in
the library; the binary owns only argument parsing, stdin, stdout, and the
terminal it draws on.

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

## What the library draws

`draw_dashboard(frame, &view)` presents that view with [ratatui] in the locked
band order — **Header → Queue → Activity → Summary** — and consults the injected
`TerminalCapabilities` for nothing but glyphs: a terminal that cannot render
Unicode gets `-`, `|`, `#` and ASCII borders in place of `—`, `•`, `█` and box
drawing, and states the identical facts.

Terminal *ownership* is the caller's. `drive_dashboard` runs the whole loop
against two injected seams — a `DashboardSurface` (anything that can be drawn on
and handed back) and an iterator of trace lines — so the behaviour the operator
depends on is drivable with no TTY at all:

- a frame before the first Event, so an idle Run is still visible;
- a frame per readable line, with an unreadable one skipped rather than fatal;
- **a final frame at end of input**, so the last state stays on screen; and
- the surface handed back exactly once, on every path including failure.

`main.rs` supplies the real surface: it opens the *controlling terminal*
(`/dev/tty`, or `CONOUT$` on Windows) rather than standard output, because the
trace arrives on standard input and the two must not contend. `crossterm` —
raw mode, the alternate screen, the cursor — appears only there, guarded by a
`Drop` that restores the terminal even on a panic.

[ratatui]: https://ratatui.rs

## What the binary does

```
git-loopy-tui [--render] [--render-at INSTANT] [--utc-offset-minutes N] \
              [--issue REF] [--model NAME] [--reasoning-effort LEVEL] \
              < events.jsonl
git-loopy-tui --schema-version
```

Reads a JSONL Event trace and, **by default**, writes the projected semantic
view as JSON. Rendering is opt-in through `--render`, which draws the live
Dashboard on the controlling terminal instead and exits `0` at end of input.
The pipeline default is deliberate: a caller that only wants the view must not
need a terminal, and the JSON path is the anti-drift control that proves the
binary adds no behaviour of its own.

`--schema-version` prints the compatibility probe and exits without reading
standard input, so an Orchestrator can decide whether to launch this helper
before committing a trace to it:

```json
{
  "name": "git-loopy-tui",
  "version": "0.2.0-dev.0",
  "min_event_schema_version": 1,
  "max_event_schema_version": 1,
  "wrapper_contract_version": "1.3"
}
```

An unreadable line is skipped rather than fatal — unusable telemetry never
blocks a render — and malformed usage exits `2`, matching the family's locked
CLI framing.

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
| `tests/dashboard_render.rs` | What each band says, read back from the fixture; ASCII fallback; the end-of-input frame and single restoration; whole-frame layout snapshots |
| `tests/standalone_helper.rs` | `--schema-version` answers without reading stdin; `--render` selects the terminal; the projection stays the default |

The rendering tests draw through ratatui's `TestBackend` and normalize the
buffer to a text grid, so they are deterministic and need no terminal. Cell
*contents* are asserted against values read back out of the fixture rather than
recomputed, and the two whole-frame grids under `tests/snapshots/` pin layout
only. Re-bless those two after an intended layout change and read the diff:

```bash
GIT_LOOPY_TUI_BLESS=1 cargo test --manifest-path git-loopy/tui/Cargo.toml \
  --test dashboard_render
```

```bash
cargo test --manifest-path git-loopy/tui/Cargo.toml
```

CI runs this, `cargo fmt --check`, and `cargo clippy -- -D warnings` in the
Runner family gate (`.github/workflows/runner-family-gate.yml`).

## Dependency floor

`serde`, `serde_json`, and `ratatui` (with default features off, and `crossterm`
as its only backend). The instant and calendar arithmetic in `src/timestamp.rs`
is hand-rolled to keep the dependency surface small for the cross-compiled
release artifacts, and it byte-matches Python's `datetime.isoformat()` because
the fixture's expected values are that format. `serde_json`'s `preserve_order`
feature is load-bearing: it makes the projected band and column order
observable, which the fixture pins.
