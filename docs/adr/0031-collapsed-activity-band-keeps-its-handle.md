# A collapsed Activity band keeps one row, so the mouse can undo what the mouse did

**Status:** accepted — supersedes the snap-to-collapsed clause of
[ADR-0021](0021-activity-windows-per-agent.md) (operator-sized Activity band). ADR-0021's
other four decisions are upheld in full.

## Context

[ADR-0021](0021-activity-windows-per-agent.md) made the **Activity** band operator-sized
and said two things that cannot both hold:

> Its **header row is the drag handle**; `shift+up` / `shift+down` are equal peers […]
> The band floors at three rows and **snaps to collapsed** below that — **the same state
> the `a` key produces**, so the two controls drive one state machine.

The state the `a` key produces is `display = False` (`app.py`,
`_App.action_toggle_activity`, issue #70), which removes the band from the Textual layout
entirely — header included — so the Queue's `1fr` can reclaim the row. A drag that snaps
into that state therefore **deletes the handle it is being performed with**. The mouse can
collapse the band and can never re-open it; only the keyboard can.

That is the same fault ADR-0021 was written to fix. Its own Context section indicts the
wheel in exactly these terms:

> The passive glance is therefore *one-way*: an accidental wheel turn permanently detaches
> it from the live tail for the rest of the Run.

A decision that reproduces the failure it diagnosed is not a decision to implement as
written. The rest of ADR-0021 — one window per **Agent**, degradation in slot order,
follow-with-release, per-window insight facts — is untouched by this and stands.

Two further gaps went unrecorded because ADR-0021 never reached implementation. It does
not say whether re-clamping on terminal resize is **destructive** of the operator's
setting, and it gives the band header no meaning for a plain click — which matters more
once collapsed is a one-row target rather than nothing at all.

## Decision

### Collapsed is a state with a handle, not an absence

- **Collapsed renders the band's one-line header and nothing else.** The handle survives
  the gesture, so a drag is undoable by a drag.
- **The `a` key adopts that same state**, replacing `display = False`. ADR-0021's claim
  that the two controls drive one state machine becomes literally true rather than
  aspirational. This is a visible change to shipped #70 behaviour: the Queue reclaims one
  row fewer, and in exchange the operator can always see that an Activity band exists.

### Intent and effect are two numbers, not one

- The band stores a **requested** height — the operator's stated intent, in absolute rows
  — and derives an **effective** height by clamping it to fit.
- **Only an explicit gesture writes `requested`.** A clamp never does. A terminal that
  shrinks and grows again therefore returns the band to the operator's height, rather than
  silently keeping whatever a transient geometry allowed. The rule also settles what `a`
  restores from collapsed, without inventing a separate default.
- **Sizing gestures** (drag, `shift+↑`, `shift+↓`) state fresh intent and write
  `requested`. **Toggle gestures** (`a`, click) preserve and restore it.

### The state machine

`effective = clamp(requested, 3, ceiling)`, where `ceiling` is the largest height that
still leaves the **Queue** its three-row floor (ADR-0021).

| Gesture | From Expanded | From Collapsed |
| --- | --- | --- |
| Drag the header | `requested` tracks the pointer; crossing below three rows → **Collapsed** | → Expanded; `requested` tracks the pointer, floored at three |
| `shift+↑` | `requested += 1`, capped at `ceiling` | → Expanded at **three** |
| `shift+↓` | `requested -= 1`; below three → **Collapsed** | no-op |
| Click the header, or `a` | → **Collapsed**; `requested` preserved | → Expanded at `requested` |
| Wheel | never resizes | never resizes |
| Terminal resize | `effective` re-clamped; `requested` untouched | stays Collapsed |

`shift+↑` out of Collapsed lands on the floor rather than restoring the remembered height,
because it is a sizing gesture and sizing gestures state intent. Nothing is lost: `a` and
the click are the restore gesture, and both are already bound.

### A click on the band header toggles collapse

A drag that ends where it began is indistinguishable from a click, so the click needs a
meaning, and a one-row drag target needs a forgiving one. Toggling keeps all three
controls driving the one state machine.

The click is bound to the **band** header, never to a **Activity window** header.
ADR-0021 reserves the latter — *"one click on a window header, which drills into that
issue's Log"* — and under ADR-0021 the band header carries the aggregate while the window
headers carry per-issue facts, so the two never collide. Drill-in already has a mouse
path: Textual's `DataTable` posts `RowSelected` on a Queue row click, which
`_open_from_queue` already handles.

### Degradation is a ladder with no dead end

**drag → click → `shift+↑`/`shift+↓`.** A terminal that reports motion gets all three; one
that reports clicks but not motion — tmux under some configurations — keeps the click; one
with no mouse reporting at all keeps the keys, which is the survival ADR-0021 asked for.

### This ADR is the normative spec for the second renderer

ADR-0021 requires that renderer behaviour *"must be reproducible from the shared contract,
not reconstructed."* The gestures above are **not** derivable from the **Event schema** — a
drag is not an Event — so a Conformance fixture would have to grow a gesture-input
dimension it does not have. The table in this ADR is the contract instead, and the Rust
port implements from it rather than from the Python source.

### Deliberately out of scope

- **`[tui] activity_height`** (ADR-0021's configured starting height) is **deferred**.
  **Config** is a family-wide parity surface (ADR-0013 phase 3), so the key would oblige
  the shell and PowerShell **Orchestrators** to carry a setting only one renderer can
  honour, and it puts a presentation knob into the Run's reproducibility surface. The
  existing named default stands until the rest of ADR-0021 lands.
- **Per-Agent Activity windows**, degradation order, follow-with-release and the
  ADR-0022 insight facts remain ADR-0021's, tracked on the parent PRD.
- The **Rust** `git-loopy-tui` gains none of this yet. It has no mouse capture, no
  hit-testing and not even the `a` key, so parity here is a clean-slate implementation
  against the table above, sequenced with ADR-0013's phase-2 parity work.

## Considered options

- **Keep ADR-0021 literally — collapse to zero rows and let `a` be the escape hatch** —
  rejected. It is the one-way gesture ADR-0021 exists to eliminate, and an operator who
  reaches for the mouse is exactly the operator least likely to know the key.
- **Floor the drag at three rows and never let it collapse** — rejected. It is safe, but
  the drag and `a` then drive two different states, giving up the single state machine
  ADR-0021 asked for in order to avoid a problem the one-row stub already solves.
- **Collapse to zero rows and make the Queue's bottom edge the grab target** — rejected.
  The handle would move depending on state, which is the invisible geometry ADR-0021
  rejects elsewhere when it refuses nested scrolling inside the band.
- **Let the wheel resize the band** — rejected. Resize-by-wheel is precisely the accidental
  gesture class ADR-0021's Context section is an argument against.
- **Bind a bare click on the band header to drill into the Active issue's Log** — rejected.
  It is natural only while there is one issue-named header, and it collides with
  ADR-0021's window-header semantics the moment windows land. Queue row-click already
  drills in.
- **Make the clamp destructive (one number)** — rejected. It makes the operator's setting
  hostage to a window manager, and it leaves `a` with nothing to restore.
- **Encode the gestures as Conformance fixtures now** — rejected for this slice. The
  fixtures map an Event trace to a rendered snapshot; gestures are a new input dimension,
  and there is nothing to drift against while the second renderer has no mouse at all. It
  becomes worth its cost once both renderers are live.

## Consequences

- `_ACTIVITY_BAND_HEIGHT` stops being the band's height and becomes only its **starting**
  `requested`. `_ActivityBand` gains the two-number state and the mouse handlers; the state
  lives on the band widget, as #70's does, so `LiveRunState` still imports no Textual
  (ADR-0001) and no **Config** or `state.py` change is implied.
- `test_activity_band_is_fixed_height_and_queue_reclaims_the_rest` asserts the invariant
  this ADR removes and is rewritten rather than deleted: the Queue still reclaims the
  space, but from a band the operator sized.
- **Collapsed** becomes a named state of the **Activity** band with defined transitions,
  so `CONTEXT.md` owes the term. Following ADR-0024's sequencing, it is recorded when the
  code implements it and not before.
- Mouse handling becomes load-bearing in the Python renderer for the first time. It had
  mouse *behaviour* already — Textual's `DataTable` click — but no mouse *handler* of
  git-loopy's own, and therefore no coverage. Both the mouse and keyboard paths are
  covered by Textual's `Pilot`, which drives `mouse_down` / `hover` / `mouse_up` and
  `resize_terminal` for the clamp-and-restore rule.
- `Pilot` injects `MouseMove` into Textual's pipeline and so cannot prove a real terminal
  reports drag motion. That risk is carried by the degradation ladder rather than by a
  test.
