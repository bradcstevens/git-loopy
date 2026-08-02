# HTML Report Format

The two-axis review is rendered as a single self-contained HTML file in the OS temp directory. Tailwind comes from a CDN; Mermaid is imported **only if** at least one diagram earns its place (spread of a Shotgun Surgery finding, a spec coverage flow). Most reviews need no diagrams at all — the findings and the quoted code carry the report.

The report is **dark mode only**. There is no light theme and no theme toggle — one palette, tuned for a dark background. It matches the palette used by the `/improve-codebase-architecture` report so both artifacts read as the same family.

The report never merges or reranks the two axes. Standards and Spec stay in separate columns, top to bottom. If the layout ever tempts you to interleave findings, the layout is wrong.

## Scaffold

```html
<!doctype html>
<html lang="en" class="dark">
  <head>
    <meta charset="utf-8" />
    <meta name="color-scheme" content="dark" />
    <title>Code review — {{repo name}} since {{fixed point}}</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <!-- Include this block ONLY if the report actually renders a diagram. -->
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
      /* small custom layer for things Tailwind doesn't cover cleanly.
         All values are tuned for a dark background. */
      .hunk { tab-size: 2; }
      .hunk .add { background: rgba(16, 185, 129, 0.08); color: #6ee7b7; }
      .hunk .del { background: rgba(248, 113, 113, 0.08); color: #fca5a5; }
      .hunk .ctx { color: #94a3b8; }
      .hunk .mark { background: rgba(251, 191, 36, 0.12); }
      /* Mermaid injects its own label colours — force them onto the dark palette. */
      .mermaid .nodeLabel, .mermaid .edgeLabel { color: #e2e8f0; fill: #e2e8f0; }
      .mermaid .edgeLabel rect { fill: #0b1120; }
    </style>
  </head>
  <body class="bg-slate-950 text-slate-200 font-sans antialiased">
    <main class="max-w-7xl mx-auto px-6 py-12 space-y-10">
      <header>...</header>
      <div class="grid md:grid-cols-2 gap-8 items-start">
        <section id="standards" class="space-y-4">...</section>
        <section id="spec" class="space-y-4">...</section>
      </div>
      <footer id="summary">...</footer>
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
| Code block background | `bg-slate-950` inside a `border-slate-800` card |
| Body text | `text-slate-200` |
| Muted / secondary text | `text-slate-400` |
| Headings | `text-slate-100` |
| Accent | `text-emerald-400` / `border-emerald-500` |
| Hard violation | `text-red-400`, `bg-red-500/10`, `border-red-500/30` |
| Judgement call / smell | `text-amber-300`, `bg-amber-500/10`, `border-amber-500/30` |
| Informational / passing | `text-emerald-300`, `bg-emerald-500/10`, `border-emerald-500/30` |
| Neutral tag | `text-slate-300`, `bg-slate-700/40`, `border-slate-600` |
| Added diff line | `#6ee7b7` on `rgba(16,185,129,0.08)` |
| Removed diff line | `#fca5a5` on `rgba(248,113,113,0.08)` |
| Quoted standard / spec line | `border-l-2 border-slate-700 text-slate-400 italic` |

Saturated 500/600 fills read as glare on dark. Use tinted overlays (`bg-red-500/10`) for badges and callouts instead of solid fills.

## Header

Repo name, the fixed point (`since <ref>`), the resolved SHA in `font-mono text-slate-400`, commit count, and files-changed count. One compact legend line, `text-xs uppercase tracking-wider text-slate-400`: red = hard violation · amber = judgement call · emerald = confirmed. No introduction paragraph — straight into the two columns.

## Two-column layout

Two sibling `<section>`s in a `grid md:grid-cols-2 gap-8 items-start`. Each gets a sticky column heading so the axis stays identifiable while scrolling:

```html
<h2 class="sticky top-0 bg-slate-950/90 backdrop-blur py-3 text-sm uppercase tracking-wider text-slate-400 border-b border-slate-800">
  Standards <span class="text-slate-600">· 4 findings</span>
</h2>
```

The columns are independent lists. Ordering within a column is that axis's own — never a global severity sort across both. Below `md`, the grid collapses to one column and Standards comes first; that stacking is layout, not ranking.

## Finding card

Each finding is one `<article class="rounded-lg border border-slate-800 bg-slate-900 p-4 space-y-3">`:

- **Badge row** — severity first, then classification. Hard violation = red badge; judgement call = amber badge. Standards findings add a source tag: `documented` (neutral) or the smell name (`Feature Envy`, `Data Clumps`, …) in amber.
- **Location** — `font-mono text-sm text-slate-400`, `path/to/file.ts:120–134`. Never a bare filename when a line range is known.
- **What** — one sentence. What the code does that's wrong.
- **Evidence** — the quoted hunk (see below). Every finding quotes code; a finding with nothing to quote isn't a finding yet.
- **Cited rule** — for Standards, the standards file plus the rule text; for Spec, the spec line. Rendered as a quote block, `border-l-2 border-slate-700 pl-3 text-slate-400 italic text-sm`.
- **Fix** — one sentence, imperative. "Move `priceFor` onto `Order`."

No paragraphs of explanation. If a finding needs three sentences to land, the evidence quote is too small — widen the quote, not the prose.

## Evidence / hunk block

Monospaced, dark, line-classed. Keep it to the smallest quote that makes the point — around 12 lines. Elide the middle with `…` rather than scrolling.

```html
<pre class="hunk overflow-x-auto rounded border border-slate-800 bg-slate-950 p-3 text-xs leading-relaxed font-mono"><code><span class="ctx"> export function submit(order) {</span>
<span class="del">-  const total = order.total;</span>
<span class="add">+  const total = order.lines.reduce((n, l) =&gt; n + l.qty * l.unitPrice, 0);</span>
<span class="mark ctx">   return gateway.charge(total);</span>
<span class="ctx"> }</span></code></pre>
```

Escape `<`, `>`, and `&` in every quoted line — unescaped diff content is the most common way this report breaks. Use `.mark` to spotlight the exact line the finding is about when the hunk has more than a couple of lines.

## Standards column

Findings only — no "here's what the repo documents" preamble. Two things distinguish the cards:

- **Documented-standard breach** — cite the standards file and quote the rule. May be a hard violation (red).
- **Baseline smell** — always a judgement call (amber), always named with the Fowler label from the skill's smell baseline. Never render a smell as a hard violation, however confident the sub-agent sounded.

If the repo documents a standard that endorses what the baseline would flag, the smell isn't a finding and doesn't appear at all.

Empty state: `<p class="text-sm text-emerald-300">No findings. Diff conforms to the documented standards and the smell baseline.</p>`

## Spec column

Group findings under three sub-headings, in this order, each `text-xs uppercase tracking-wider text-slate-500`:

1. **Missing or partial** — the spec asked, the diff doesn't deliver. Red.
2. **Unasked-for behaviour** — scope creep. Amber.
3. **Implemented but wrong** — present, but doesn't do what the spec described. Red.

Every card quotes the spec line it's judged against. Omit a sub-heading entirely when it has no findings — don't render empty groups.

No-spec state: a single card, neutral not alarming — `<p class="text-slate-400">No spec available. Spec axis skipped.</p>` with one line on what was searched (commit message refs, `docs/`, `specs/`, `.scratch/`).

## Optional diagram

Reach for Mermaid only when a finding is genuinely shaped like a graph — Shotgun Surgery spread across files, a Message Chain walk, a spec requirement fanning into several modules. Wrap it in the standard card and colour the offending nodes red:

```html
<div class="rounded-lg border border-slate-800 bg-slate-900 p-4">
  <pre class="mermaid">
    flowchart LR
      A[submit] --> B[OrderTotals]
      A --> C[Invoice]
      A --> D[Ledger]
      classDef hit stroke:#f87171,stroke-width:2px,fill:#1e293b,color:#fecaca;
      class B,C,D hit
  </pre>
</div>
```

One diagram per report at most. If nothing is graph-shaped, drop the Mermaid import from the scaffold entirely.

## Summary footer

One full-width card. Per-axis counts (`Standards: 4 · Spec: 2`), then the worst issue **within each axis**, each as its own line with an anchor link to the card.

Never a combined verdict, a single "top issue", a merged severity list, or a score. The whole point of two axes is that one can't mask the other — a footer that ranks across axes undoes the report.

## Style guidance

- Dark mode only. No `dark:` variants, no toggle, no light fallback — write the dark values directly.
- Depth comes from surface and border steps (`slate-950` → `slate-900` → `slate-800`), not shadows. Drop shadows are invisible on dark; use `ring-1 ring-slate-800` if a card needs lift.
- Never emit a bare `bg-white`, `bg-gray-50`, `text-black`, or `text-slate-900`. Inline SVG, syntax spans, and Mermaid `classDef` fills are the usual places light values sneak back in — check all three.
- Colour carries meaning here: red = hard, amber = judgement, emerald = clean. Don't spend those three on decoration.
- Generous whitespace between cards (`space-y-4`), tight inside them. Code blocks scroll horizontally (`overflow-x-auto`); the page never does.
- The only scripts are the Tailwind CDN and — when a diagram exists — the Mermaid ESM import. The report is static: no filtering UI, no collapsibles, no app code.

## Tone

Plain English, concise, specific. Name the file, the symbol, the rule. Findings read as observations, not verdicts on the author.

- **Write:** "`submit` reads three fields off `Order` and none of its own — possible Feature Envy."
- **Not:** "This code is poorly designed and should be refactored."

Hard violations state the breach and the rule. Judgement calls say so in the words, not just the badge — "possible", "looks like", "worth a second opinion". Never inflate a smell into a violation to make the report feel weightier.

No hedging elsewhere, no throat-clearing, no "it's worth noting that…". If a sentence could be a bullet, make it a bullet. If a finding can't cite a rule, a spec line, or a named smell, it doesn't go in the report.
