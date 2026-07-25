# HTML Report Format

The architectural review is rendered as a single self-contained HTML file in the OS temp directory. Tailwind and Mermaid both come from CDNs. Mermaid handles graph-shaped diagrams reliably; hand-built divs and inline SVG handle the more editorial visuals (mass diagrams, cross-sections). Mix the two — don't lean on Mermaid for everything, it'll start to look generic.

The report is **dark mode only**. There is no light theme and no theme toggle — one palette, tuned for a dark background.

## Scaffold

```html
<!doctype html>
<html lang="en" class="dark">
  <head>
    <meta charset="utf-8" />
    <meta name="color-scheme" content="dark" />
    <title>Architecture review — {{repo name}}</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script type="module">
      import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs";
      mermaid.initialize({
        startOnLoad: true,
        theme: "base",
        securityLevel: "loose",
        themeVariables: {
          darkMode: true,
          background: "#0b1120",
          primaryColor: "#1e293b",
          primaryTextColor: "#e2e8f0",
          primaryBorderColor: "#475569",
          secondaryColor: "#0f172a",
          tertiaryColor: "#111827",
          lineColor: "#94a3b8",
          textColor: "#cbd5e1",
          mainBkg: "#1e293b",
          nodeBorder: "#475569",
          clusterBkg: "#0f172a",
          clusterBorder: "#334155",
          edgeLabelBackground: "#0b1120",
          fontFamily: "ui-sans-serif, system-ui, sans-serif",
        },
      });
    </script>
    <style>
      /* small custom layer for things Tailwind doesn't cover cleanly:
         dashed seam lines, hand-drawn-feeling arrow heads, etc.
         All values are tuned for a dark background. */
      .seam { stroke-dasharray: 4 4; stroke: #64748b; }
      .leak { stroke: #f87171; }
      .deep { background: linear-gradient(135deg, #1e293b, #0f172a); }
      /* Mermaid injects its own label colours — force them onto the dark palette. */
      .mermaid .nodeLabel, .mermaid .edgeLabel { color: #e2e8f0; fill: #e2e8f0; }
      .mermaid .edgeLabel rect { fill: #0b1120; }
    </style>
  </head>
  <body class="bg-slate-950 text-slate-200 font-sans antialiased">
    <main class="max-w-5xl mx-auto px-6 py-12 space-y-12">
      <header>...</header>
      <section id="candidates" class="space-y-10">...</section>
      <section id="top-recommendation">...</section>
    </main>
  </body>
</html>
```

## Palette

One palette, no light variant. Use these classes rather than inventing new ones:

| Role | Class / value |
| --- | --- |
| Page background | `bg-slate-950` |
| Card background | `bg-slate-900` |
| Card border | `border-slate-800` |
| Body text | `text-slate-200` |
| Muted / secondary text | `text-slate-400` |
| Headings | `text-slate-100` |
| Accent | `text-emerald-400` / `border-emerald-500` |
| Leakage (red) | `text-red-400`, stroke `#f87171` |
| Warning / ADR (amber) | `text-amber-300`, `bg-amber-500/10`, `border-amber-500/30` |
| Deep module fill | `.deep` gradient, `border-slate-500` |
| Faded / inert internals | `text-slate-600`, `opacity-50` |

Saturated 500/600 fills read as glare on dark. Use tinted overlays (`bg-emerald-500/10`) for badge and callout backgrounds instead of solid fills.

## Header

Repo name, date, and a compact legend: solid box = module, dashed line = seam, red arrow = leakage, thick light-bordered box = deep module. No introduction paragraph — straight into the candidates.

## Candidate card

The diagrams carry the weight. Prose is sparse, plain, and uses the glossary terms (from the `/codebase-design` skill) without ceremony.

Each candidate is one `<article>`:

- **Title** — short, names the deepening (e.g. "Collapse the Order intake pipeline").
- **Badge row** — recommendation strength (`Strong` = `bg-emerald-500/10 text-emerald-300 border-emerald-500/30`, `Worth exploring` = amber equivalent, `Speculative` = `bg-slate-700/40 text-slate-300 border-slate-600`), plus a tag for the dependency category (`in-process`, `local-substitutable`, `ports & adapters`, `mock`).
- **Files** — monospaced list, `font-mono text-sm text-slate-400`.
- **Before / After diagram** — the centrepiece. Two columns, side by side. See patterns below.
- **Problem** — one sentence. What hurts.
- **Solution** — one sentence. What changes.
- **Wins** — bullets, ≤6 words each. e.g. "Tests hit one interface", "Pricing logic stops leaking", "Delete 4 shallow wrappers".
- **ADR callout** (if applicable) — one line in `bg-amber-500/10 border border-amber-500/30 text-amber-200`.

No paragraphs of explanation. If the diagram needs a paragraph to be understood, redraw the diagram.

## Diagram patterns

Pick the pattern that fits the candidate. Mix them. Don't make every diagram look the same — variety is part of the point.

### Mermaid graph (the workhorse for dependencies / call flow)

Use a Mermaid `flowchart` or `graph` when the point is "X calls Y calls Z, and look at the mess." Wrap it in a Tailwind-styled card so it doesn't feel parachuted in. Style with classDef to colour leakage edges red and the deep module a lighter slate fill with a bright border. Sequence diagrams work well for "before: 6 round-trips; after: 1."

```html
<div class="rounded-lg border border-slate-800 bg-slate-900 p-4">
  <pre class="mermaid">
    flowchart LR
      A[OrderHandler] --> B[OrderValidator]
      B --> C[OrderRepo]
      C -.leak.-> D[PricingClient]
      classDef leak stroke:#f87171,stroke-width:2px,fill:#1e293b,color:#fecaca;
      class C,D leak
  </pre>
</div>
```

### Hand-built boxes-and-arrows (when Mermaid's layout fights you)

Modules as `<div>`s with borders and labels. Arrows as inline SVG `<line>` or `<path>` elements — set `stroke="#94a3b8"` (never `currentColor` defaults that resolve to black) — positioned absolutely over a relative container. Reach for this when you want the "after" diagram to feel like one thick light-bordered deep module with dimmed internals (`text-slate-600`) — Mermaid won't render that with the right weight.

### Cross-section (good for layered shallowness)

Stack horizontal bands (`h-12 border-l-4`) to show layers a call passes through. Before: 6 thin layers each doing nothing. After: 1 thick band labelled with the consolidated responsibility.

### Mass diagram (good for "interface as wide as implementation")

Two rectangles per module — one for interface surface area, one for implementation. Before: interface rectangle is nearly as tall as the implementation rectangle (shallow). After: interface rectangle is short, implementation rectangle is tall (deep).

### Call-graph collapse

Before: a tree of function calls rendered as nested boxes. After: the same tree collapsed into one box, with the now-internal calls shown dimmed inside it (`text-slate-600`, `opacity-50` — dimming means *darker* here, not lighter).

## Style guidance

- Dark mode only. No `dark:` variants, no toggle, no light fallback — write the dark values directly.
- Lean editorial, not corporate-dashboard. Generous whitespace. Serif optional for headings (`font-serif` works well with slate).
- Depth comes from border and surface steps (`slate-950` → `slate-900` → `slate-800`), not shadows. Drop shadows are invisible on dark; use `ring-1 ring-slate-800` if a card needs lift.
- Colour sparingly: one accent (emerald) plus red for leakage and amber for warnings — all at the 300/400 end of the scale so they stay legible on `slate-950`.
- Never emit a bare `bg-white`, `bg-stone-50`, `text-black`, or `text-slate-900`. Inline SVG and Mermaid `classDef` fills are the usual places light values sneak back in — check both.
- Keep diagrams ~320px tall so before/after sits comfortably side by side without scrolling.
- Use `text-xs uppercase tracking-wider text-slate-400` for module labels inside diagrams — they should read as schematic, not as UI.
- The only scripts are the Tailwind CDN and the Mermaid ESM import. The report is otherwise static — no app code, no interactivity beyond Mermaid's own rendering.

## Top recommendation section

One larger card. Candidate name, one sentence on why, anchor link to its card. That's it.

## Tone

Plain English, concise — but the architectural nouns and verbs come straight from the `/codebase-design` skill. Concision is not an excuse to drift.

**Use exactly:** module, interface, implementation, depth, deep, shallow, seam, adapter, leverage, locality.

**Never substitute:** component, service, unit (for module) · API, signature (for interface) · boundary (for seam) · layer, wrapper (for module, when you mean module).

**Phrasings that fit the style:**

- "Order intake module is shallow — interface nearly matches the implementation."
- "Pricing leaks across the seam."
- "Deepen: one interface, one place to test."
- "Two adapters justify the seam: HTTP in prod, in-memory in tests."

**Wins bullets** name the gain in glossary terms: *"locality: bugs concentrate in one module"*, *"leverage: one interface, N call sites"*, *"interface shrinks; implementation absorbs the wrappers"*. Don't write *"easier to maintain"* or *"cleaner code"* — those terms aren't in the glossary and don't earn their place.

No hedging, no throat-clearing, no "it's worth noting that…". If a sentence could be a bullet, make it a bullet. If a bullet could be cut, cut it. If a term isn't in the `/codebase-design` glossary, reach for one that is before inventing a new one.
