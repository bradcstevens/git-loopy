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

`draw_frame(frame, &dashboard)` presents a `DashboardFrame` — a view plus the
screen the operator is on, the issue under their cursor, the terminal's
capabilities, and any bounded diagnostics — with [ratatui]. Two screens, each in
a locked band order:

| Screen | Bands |
| --- | --- |
| Dashboard | **Header → Queue → Activity → Summary** |
| Drill-in | **Detail header → Iteration breakdown → Log** |

Capabilities move glyphs and nothing else: a terminal that cannot render Unicode
gets `-`, `|`, `#` and ASCII borders in place of `—`, `•`, `█` and box drawing,
and states the identical facts.

### Navigation

`Screen`, `Key` and `DashboardSession::handle_key` are the whole model; `main.rs`
only decides which key press means which `Key`.

| Intent | Keys |
| --- | --- |
| Move through the Queue | `↑`/`k`, `↓`/`j`, `Home`/`g`, `End`/`G` |
| Open the selected issue | `Enter`, `→`, `l` |
| Back to the Dashboard | `Esc`, `Backspace`, `←`, `h` |
| Quit | `q`, `Ctrl-C`, `Ctrl-D` |

The cursor holds an **issue, not a row**. The Queue groups active before queued
before history, so a row moves the moment an issue is activated, and a
positional cursor would silently retarget under the operator's hands.

### Narrow terminals

Above the floor, reduction gives up whole facts in importance order rather than
truncating one: Queue, Summary and Iteration-breakdown columns drop from the
least decisive, and the Header sheds its run id before its model and its context
gauge before its status. `strikes 0/` and `strikes 0/3` look alike at a glance
and mean different things, so nothing is ever cut mid-word. Below **40×12** the
whole layout is replaced by an unbordered ASCII state naming the size it has,
the size it needs, and the `--render`-free way out.

### Input the operator did not send

`drive_dashboard` runs the whole loop against two injected seams — a
`DashboardSurface` (anything that can be drawn on and handed back) and an
iterator of `Input` — so the behaviour the operator depends on is drivable with
no TTY at all:

- a frame before the first Event, so an idle Run is still visible;
- a frame per readable line, with an unreadable one skipped rather than fatal;
- **a final frame at end of input**, so the last state stays on screen; and
- the surface handed back exactly once, on every path including failure.

`InputQueue` is the bounded channel between the readers and the loop. Structural
input — trace lines, key presses, the end of the trace — is **never dropped**: a
full buffer hands it straight back and the reader waits, which pushes back on
the pipe instead of losing a Run's history. Only *render-only* deltas may
coalesce to the newest value, and only three qualify: a context-window sample, a
resize, and a clock tick. None of them changes what the Run did.

Unusable telemetry leaves a bounded diagnostic — `input 12 unreadable` in the
Header, with one truncated example kept on the session — rather than a stream
scrolling the Run out of view. An *unknown* Event type is not a diagnostic: it
decodes, reduces to nothing, and is simply skipped.

`main.rs` supplies the real surface and the real readers: a dedicated thread
owning standard input so a slow frame can never stall the Orchestrator's pipe, a
second reading the *controlling terminal* (`/dev/tty`, or `CONOUT$` on Windows)
so the keyboard and the trace never contend for a byte, and a half-second tick so
elapsed timers keep moving through a quiet stretch. `crossterm` — raw mode, the
alternate screen, the cursor — appears only there, guarded by a `Drop` **and** a
panic hook, so raw mode never follows the operator out.

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
Dashboard on the controlling terminal instead and exits `0` at end of input or
when the operator quits.
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
  "wrapper_contract_version": "1.4"
}
```

An unreadable line is skipped rather than fatal — unusable telemetry never
blocks a render — and malformed usage exits `2`, matching the family's locked
CLI framing. Presentation input that fails *unrecoverably* (the trace pipe
breaks, or no terminal can be opened) exits `1` after naming the reason on
standard error, and writes nothing at all to standard output: the Run's stdout
belongs to the Orchestrator, and a viewer's failure must never look like the
Run's.

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
| `tests/dashboard_render.rs` | What each Dashboard band says, read back from the fixture; ASCII fallback; the end-of-input frame and single restoration; whole-frame layout snapshots |
| `tests/drill_in_render.rs` | What each drill-in band says, read back from the fixture; the locked band order; ASCII fallback; whole-frame layout snapshot |
| `tests/navigation.rs` | Moving, opening, returning, and quitting; selection held by issue rather than row |
| `tests/responsive_render.rs` | Column and Header reduction in importance order, never mid-word; the minimum-size state; narrow and below-floor snapshots |
| `tests/bounded_input.rs` | Structural input is never dropped; only render-only deltas coalesce, to the newest value |
| `tests/run_loop.rs` | Quitting, ticks, an unrecoverable read, bounded diagnostics, and restoration on every exit path |
| `tests/log_guarantees.rs` | Logs are bounded per issue, retain pre-activation output with its own instants, and span Iterations |
| `tests/standalone_helper.rs` | `--schema-version` answers without reading stdin; `--render` selects the terminal; a mostly-unreadable trace still finishes, silently on stdout; the projection stays the default |

The rendering tests draw through ratatui's `TestBackend` and normalize the
buffer to a text grid, so they are deterministic and need no terminal. Cell
*contents* are asserted against values read back out of the fixture rather than
recomputed, and the whole-frame grids under `tests/snapshots/` pin layout only.
Re-bless them after an intended layout change and read the diff:

```bash
GIT_LOOPY_TUI_BLESS=1 cargo test --manifest-path git-loopy/tui/Cargo.toml
```

```bash
cargo test --manifest-path git-loopy/tui/Cargo.toml
```

CI runs this, `cargo fmt --check`, and `cargo clippy -- -D warnings` in the
Runner family gate (`.github/workflows/runner-family-gate.yml`).

## Release artifacts

A tagged Release publishes seven prebuilt helpers, built by
[`.github/workflows/tui-release.yml`](../../.github/workflows/tui-release.yml)
from pinned cargo-dist `0.32.0`:

| Target | Runner | Built |
| --- | --- | --- |
| `aarch64-apple-darwin` | `macos-14` | natively |
| `x86_64-apple-darwin` | `macos-13` | natively |
| `x86_64-pc-windows-msvc` | `windows-2022` | natively |
| `x86_64-unknown-linux-gnu` | `ubuntu-22.04` | natively |
| `x86_64-unknown-linux-musl` | `ubuntu-22.04` | natively (statically linked, so it runs on the glibc runner that produced it) |
| `aarch64-unknown-linux-gnu` | `ubuntu-22.04` | cross, in a container |
| `aarch64-unknown-linux-musl` | `ubuntu-22.04` | cross, in a container |

Windows arm64, 32-bit ARM Linux, and FreeBSD are **deferred by name** in
[`../conformance/tui-artifacts.json`](../conformance/tui-artifacts.json) rather
than silently absent, so an operator on one of them reads why instead of the
same "no artifact" sentence a typo would produce.

The archives are named `git-loopy-tui-<target>.tar.xz` (`.zip` on Windows) with
no version in the filename — a Release addresses its artifacts by tag, and a
filename that repeated the version would give a mismatched download two ways to
look right. Identity is proven three other ways instead: a published
`.sha256` beside every archive, a GitHub artifact attestation, and the helper's
own `--version`, which reports the single distribution Release version
([ADR-0016](../../docs/adr/0016-single-distribution-release-version.md)).

Before publication each artifact is verified through
`python -m git_loopy.tui_release`. A natively built one is *run*: it must claim
this Release, accept this Event schema, drain a minimal Run-start/Run-end trace,
and exit cleanly. A cross-built one cannot execute on its release runner, so its
job proves build and metadata identity and says so rather than pretending it ran.
Publication is all-or-nothing — an incomplete set leaves one platform's installer
resolving a name that 404s.

## Dependency floor

`serde`, `serde_json`, and `ratatui` (with default features off, and `crossterm`
as its only backend). The instant and calendar arithmetic in `src/timestamp.rs`
is hand-rolled to keep the dependency surface small for the cross-compiled
release artifacts, and it byte-matches Python's `datetime.isoformat()` because
the fixture's expected values are that format. `serde_json`'s `preserve_order`
feature is load-bearing: it makes the projected band and column order
observable, which the fixture pins.
