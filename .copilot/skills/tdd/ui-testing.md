# UI/UX Testing with playwright-cli

For any browser-based UI or UX work, use the **[playwright-cli](../playwright-cli/SKILL.md)** skill as the canonical driver for test-driven development. The same TDD philosophy applies: test observable user-facing behavior through the public interface, not implementation details.

## The UI's public interface is accessibility

In a browser, the **public interface is what a user can perceive and interact with**: roles, labels, accessible names, visible text, headings, regions, and interactive controls. Internal DOM structure, CSS class names, framework state, and component hierarchies are **implementation details**.

This is why playwright-cli's accessibility-tree snapshot is the right primitive: it shows you exactly what the user (and assistive technology) can see, and nothing more.

> If a control cannot be found semantically (by role, label, name, or visible text), it is both harder to test **and** less accessible. Designing for one improves the other.

## Two roles for playwright-cli

playwright-cli plays two distinct roles in UI TDD. Don't conflate them.

1. **Exploration & authoring driver** (interactive CLI session)
   Use the CLI to open the browser, navigate, take snapshots, try interactions, and figure out the right semantic locators for the behavior you want to test. The snapshot reveals what semantic anchors exist (roles, names, labels) and how the user-facing flow actually works.

2. **Durable regression test** (committed Playwright test code)
   The **TDD artifact is committed Playwright test code** using semantic locators — not an ad-hoc CLI session. CLI sessions are exploratory; tests are persistent. After exploring with the CLI, commit a Playwright test (`*.spec.ts` / `*.spec.js` / equivalent) that can run repeatedly in CI.

The playwright-cli skill's [test-generation reference](../playwright-cli/references/test-generation.md) helps convert a CLI session into committed test code.

## Reconnaissance: understand the page before you write tests

Before you can write a meaningful RED test for an existing or in-progress UI, you have to know what the page actually does. This is especially true when **testing a third-party flow, regression-testing a complex page, or building a replica of an existing site**. Skipping reconnaissance means you'll write tests for behavior that doesn't exist, or miss the behavior that does — and you'll commit to the wrong interaction model before you understand the real one.

This reconnaissance work happens inside the **exploration & authoring driver** role of playwright-cli (Role 1 above). The output is notes that inform your RED tests; the tests themselves stay semantic and behavior-focused.

### Understand how it looks AND how it behaves

A website is not a screenshot — it's a living thing. Elements move, change, appear, and disappear in response to scrolling, hovering, clicking, resizing, and time.

For every element you plan to test, extract its **appearance** (exact computed CSS via `getComputedStyle()`) AND its **behavior** (what changes, what triggers the change, and how the transition happens). Not "it looks like 16px" — extract the actual computed value. Not "the nav changes on scroll" — document the exact trigger (scroll position, IntersectionObserver threshold, viewport intersection), the before and after states (both sets of CSS values), and the transition (duration, easing, CSS transition vs. JS-driven vs. CSS `animation-timeline`).

Examples of behaviors to watch for — illustrative, not exhaustive. The page may do things not on this list, and your tests must cover those too:

- A navbar that shrinks, changes background, or gains a shadow after scrolling past a threshold
- Elements that animate into view when they enter the viewport (fade-up, slide-in, stagger delays)
- Sections that snap into place on scroll (`scroll-snap-type`)
- Parallax layers that move at different rates than the scroll
- Hover states that animate (not just change — the transition duration and easing matter)
- Dropdowns, modals, accordions with enter/exit animations
- Scroll-driven progress indicators or opacity transitions
- Auto-playing carousels or cycling content
- Dark-to-light (or any theme) transitions between page sections
- **Tabbed/pill content that cycles** — buttons that switch visible card sets with transitions
- **Scroll-driven tab/accordion switching** — sidebars where the active item auto-changes as content scrolls past (IntersectionObserver, NOT click handlers)
- **Smooth scroll libraries** (Lenis, Locomotive Scroll) — check for `.lenis` class or scroll container wrappers

### Identify the interaction model before writing tests

This is the single most expensive mistake in UI testing: writing a click-based test when the section is scroll-driven, or vice versa. Before authoring a RED test for an interactive section, you must definitively answer: **Is this section driven by clicks, scrolls, hovers, time, or some combination?**

How to determine this:

1. **Don't click first.** Scroll through the section slowly and observe if things change on their own as you scroll.
2. If they do, it's scroll-driven. Extract the mechanism: `IntersectionObserver`, `scroll-snap`, `position: sticky`, `animation-timeline`, or JS scroll listeners.
3. If nothing changes on scroll, THEN click/hover to test for click/hover-driven interactivity.
4. Document the interaction model explicitly in the test (in a comment or `describe` block): "INTERACTION MODEL: scroll-driven with IntersectionObserver" or "INTERACTION MODEL: click-to-switch with opacity transition."

A section with a sticky sidebar and scrolling content panels is fundamentally different from a tabbed interface where clicking switches content. Getting this wrong means a complete test rewrite, not a one-line tweak.

### Extract every state, not just the default

Many components have multiple visual states — a tab bar shows different cards per tab, a header looks different at scroll position 0 vs 100, a card has hover effects. You must extract ALL states, not just whatever is visible on page load, so each state gets its own RED test.

For tabbed/stateful content:

- Click each tab/button via the playwright-cli session
- Extract the content, images, and card data for EACH state
- Record which content belongs to which state
- Note the transition animation between states (opacity, slide, fade, etc.)
- Write a test per state

For scroll-dependent elements:

- Capture computed styles at scroll position 0 (initial state)
- Scroll past the trigger threshold and capture computed styles again (scrolled state)
- Diff the two to identify exactly which CSS properties change
- Record the transition CSS (duration, easing, properties)
- Record the exact trigger threshold (scroll position in px, or viewport intersection ratio)
- Write tests for both states (before threshold and after)

### Mandatory interaction sweep

A dedicated reconnaissance pass that runs AFTER you take baseline screenshots and BEFORE you write any tests. Its purpose is to discover every behavior on the page — many of which are invisible in a static screenshot.

**Scroll sweep:** Scroll the page slowly from top to bottom via playwright-cli. At each section, pause and observe:

- Does the header change appearance? Record the scroll position where it triggers.
- Do elements animate into view? Record which ones and the animation type.
- Does a sidebar or tab indicator auto-switch as you scroll? Record the mechanism.
- Are there scroll-snap points? Record which containers.
- Is there a smooth scroll library active? Check for non-native scroll behavior.

**Click sweep:** Click every element that looks interactive:

- Every button, tab, pill, link, card
- Record what happens: does content change? Does a modal open? Does a dropdown appear?
- For tabs/pills: click EACH ONE and record the content that appears for each state

**Hover sweep:** Hover over every element that might have hover states:

- Buttons, cards, links, images, nav items
- Record what changes: color, scale, shadow, underline, opacity

**Responsive sweep:** Test at 3 viewport widths via playwright-cli:

- Desktop: 1440px
- Tablet: 768px
- Mobile: 390px
- At each width, note which sections change layout (column → stack, sidebar disappears, etc.) and at approximately which breakpoint the change occurs.

Save all findings to a reconnaissance document (e.g., `docs/research/BEHAVIORS.md`). This is your behavior bible — reference it when writing every test.

### Page topology

Map every distinct section of the page from top to bottom. Give each a working name. Document:

- Their visual order
- Which are fixed/sticky overlays vs. flow content
- The overall page layout (scroll container, column structure, z-index layers)
- Dependencies between sections (e.g., a floating nav that overlays everything)
- **The interaction model** of each section (static, click-driven, scroll-driven, time-driven)

Save this alongside the behavior notes (e.g., `docs/research/PAGE_TOPOLOGY.md`) — it becomes your test assembly blueprint.

### Reconnaissance for cloning an existing site

If you're testing a replica you're building of a third-party site, capture these global details up front so your tests can assert them:

**Screenshots** — Take full-page screenshots at desktop (1440px) and mobile (390px) viewports. Save to `docs/design-references/` with descriptive names. These are your master reference — builders will receive section-specific crops/screenshots later.

**Fonts** — Inspect `<link>` tags for Google Fonts or self-hosted fonts. Check computed `font-family` on key elements (headings, body, code, labels). Document every family, weight, and style actually used. Configure them in your app entry point (e.g., `src/app/layout.tsx` using `next/font/google` or `next/font/local` for Next.js projects).

**Colors** — Extract the site's color palette from computed styles across the page. Update your global stylesheet (e.g., `src/app/globals.css`) with the target's actual colors in the `:root` and `.dark` CSS variable blocks. Map them to your design system's token names (background, foreground, primary, muted, etc.) where they fit. Add custom properties for colors that don't map cleanly.

**Favicons & meta** — Download favicons, apple-touch-icons, OG images, webmanifest to `public/seo/` (or your equivalent). Update document metadata accordingly.

**Global UI patterns** — Identify any site-wide CSS or JS: custom scrollbar hiding, scroll-snap on the page container, global keyframe animations, backdrop filters, gradients used as overlays, **smooth scroll libraries** (Lenis, Locomotive Scroll — check for `.lenis`, `.locomotive-scroll`, or custom scroll container classes). Add these to your global stylesheet and note any libraries that need to be installed.

### Asset discovery script pattern

Use playwright-cli's `evaluate` (or the equivalent browser MCP) to enumerate all assets on the page so you know what to test for:

```javascript
// Run this via playwright-cli evaluate to discover all assets
JSON.stringify({
  images: [...document.querySelectorAll('img')].map(img => ({
    src: img.src || img.currentSrc,
    alt: img.alt,
    width: img.naturalWidth,
    height: img.naturalHeight,
    // Include parent info to detect layered compositions
    parentClasses: img.parentElement?.className,
    siblings: img.parentElement ? [...img.parentElement.querySelectorAll('img')].length : 0,
    position: getComputedStyle(img).position,
    zIndex: getComputedStyle(img).zIndex
  })),
  videos: [...document.querySelectorAll('video')].map(v => ({
    src: v.src || v.querySelector('source')?.src,
    poster: v.poster,
    autoplay: v.autoplay,
    loop: v.loop,
    muted: v.muted
  })),
  backgroundImages: [...document.querySelectorAll('*')].filter(el => {
    const bg = getComputedStyle(el).backgroundImage;
    return bg && bg !== 'none';
  }).map(el => ({
    url: getComputedStyle(el).backgroundImage,
    element: el.tagName + '.' + el.className?.split(' ')[0]
  })),
  svgCount: document.querySelectorAll('svg').length,
  fonts: [...new Set([...document.querySelectorAll('*')].slice(0, 200).map(el => getComputedStyle(el).fontFamily))],
  favicons: [...document.querySelectorAll('link[rel*="icon"]')].map(l => ({ href: l.href, sizes: l.sizes?.toString() }))
});
```

## RED → GREEN → REFACTOR for UI

The same vertical-slice loop from the [main workflow](SKILL.md) applies to UI work:

### RED — write one failing UI test

- Pick **one** user-visible behavior (e.g., "user submits the signup form and sees a confirmation").
- Optionally explore the intended flow with `playwright-cli open <url>` + `snapshot` to discover the semantic anchors you'll target.
- Write **one** Playwright test that exercises that flow end-to-end through semantic locators. Run it. It must fail for the right reason (behavior missing), not the wrong reason (test broken).

### GREEN — minimal UI to pass

- Implement the smallest amount of UI/markup/state/backend wiring needed for that one test to pass.
- Verify in the browser via the CLI if helpful (`playwright-cli goto …; snapshot`), but the source of truth is the committed test passing.

### REFACTOR — change structure, not behavior

- Restructure components, swap CSS frameworks, rename internal state, extract sub-components — the semantic test should keep passing because it asserts on what the user sees, not how it's built.
- If a refactor breaks the test and no user-visible behavior changed, the test is over-coupled to implementation. Fix the test, then refactor.

## Locators: semantic, not ephemeral

```bash
# Exploration (CLI)
playwright-cli snapshot
# → reveals refs like e3, e5, e12 plus their roles/names
playwright-cli click e3
```

**The refs `e3`, `e5`, `e12` are ephemeral handles for the current snapshot.** They are not stable identifiers and will shift between snapshots and sessions. **Never** commit them into durable tests, docs, or assertions.

In committed Playwright tests, use semantic locators:

```ts
// GOOD: semantic locators - survive markup refactors
await page.getByRole('button', { name: 'Sign up' }).click();
await page.getByLabel('Email').fill('alice@example.com');
await expect(page.getByRole('heading', { name: 'Welcome, Alice' })).toBeVisible();

// BAD: ephemeral CLI refs, CSS, or DOM structure - brittle
await page.click('e3');                                  // ephemeral
await page.click('.btn-primary.signup-cta');             // CSS class
await page.click('div > form > button:nth-child(3)');    // DOM structure
await page.locator('[data-testid="signup-btn-v2"]').click(); // test-only id leaks impl
```

Prefer (in order): `getByRole` → `getByLabel` → `getByText` → `getByPlaceholder` → `getByTitle`. Fall back to `data-testid` only when no semantic anchor exists, and treat that as a signal the UI could be more accessible.

## Assertions: targeted semantic, not full-snapshot

```ts
// GOOD: targeted, behavior-focused
await expect(page.getByRole('heading', { name: 'Order confirmed' })).toBeVisible();
await expect(page.getByText(/Receipt sent to alice@example\.com/)).toBeVisible();

// BAD: whole-tree snapshot matching - drifts on any unrelated change
expect(await page.accessibility.snapshot()).toMatchSnapshot();
```

Full accessibility-tree or DOM snapshots cause noisy failures on incidental changes. Reserve snapshot matching for explicit accessibility-regression or visual-regression tests where that **is** the behavior under test.

## Network mocking in browser tests

Apply the same rule from [mocking.md](mocking.md): mock at **system boundaries**, not internal collaborators.

- **Mock third-party services** (payment, analytics, external APIs) using `playwright-cli route` during exploration and `page.route(…)` in committed tests. These are real boundaries.
- **Prefer a real local/test backend for your own APIs.** Mocking your own backend in UI tests lets the frontend pass while the real frontend↔backend contract is broken. Use a test backend, in-memory DB, or seeded fixtures instead.
- **Route mocking is valid for**: error-state and edge-case tests, latency/timeout simulation, rare scenarios that are hard to reproduce against the real backend.
- **Set routes before the request fires.** Register `route` (or `page.route`) before navigating or triggering the action, otherwise the mock won't apply.

```bash
# Exploration: simulate a 500 from a third-party API
playwright-cli route "https://api.stripe.com/**" --status=500
playwright-cli click e3   # submit form
playwright-cli snapshot   # verify user-facing error message
```

```ts
// Committed test
await page.route('https://api.stripe.com/**', route => route.fulfill({ status: 500 }));
await page.goto('/checkout');
await page.getByRole('button', { name: 'Pay' }).click();
await expect(page.getByText(/payment service unavailable/i)).toBeVisible();
```

## Flakiness guardrails

Browser tests fail in ways unit tests don't. To keep them reliable:

- **Assert on observable state, not arbitrary `sleep`/`waitForTimeout`.** Use `expect(...).toBeVisible()`, `toHaveText()`, etc. — Playwright auto-waits.
- **Register network routes before triggering the request.** Late registration silently misses the call.
- **Isolate state between tests.** Close the browser session, clear cookies/localStorage, or use a fresh `context` per test. State leakage causes order-dependent failures.
- **Use deterministic test data.** Avoid `Date.now()`, random IDs, or live external dependencies in assertions.
- **Don't assert on transient indicators** (spinners, toast that auto-dismiss) unless that's the behavior being tested — race conditions.
- **Screenshots, video, and tracing are diagnostics, not assertions.** Use them to debug a failure, not as the primary check. The [tracing reference](../playwright-cli/references/tracing.md) covers diagnostic capture.
- **Accessibility-tree snapshots don't catch visual regressions** (color, spacing, overflow). If visual fidelity matters, add explicit screenshot or visual-regression tests separately.

## Per-cycle checklist for UI work

```
[ ] Interaction model identified (click/scroll/hover/time) before authoring this test
[ ] Test exercises a user-visible behavior end-to-end through the browser
[ ] Locators are semantic (role/label/text), not ephemeral refs, CSS, or DOM paths
[ ] Assertions are targeted on user-observable outcomes, not whole-snapshot diffs
[ ] Only system-boundary network calls are mocked; own backend is real where feasible
[ ] Test would survive a markup/CSS/framework refactor
[ ] Test is committed (Playwright code), not just an ad-hoc CLI session
[ ] No arbitrary sleeps; assertions use auto-waiting matchers
```

## Cross-references

- Main loop and philosophy: [SKILL.md](SKILL.md)
- Good vs bad tests (general): [tests.md](tests.md)
- Mocking principles: [mocking.md](mocking.md)
- Interface design for testability: [interface-design.md](interface-design.md)
- playwright-cli command reference: [../playwright-cli/SKILL.md](../playwright-cli/SKILL.md)
- Converting a CLI session into committed tests: [../playwright-cli/references/test-generation.md](../playwright-cli/references/test-generation.md)
- Request/route mocking details: [../playwright-cli/references/request-mocking.md](../playwright-cli/references/request-mocking.md)
- Storage state (auth, cookies): [../playwright-cli/references/storage-state.md](../playwright-cli/references/storage-state.md)
- Tracing for diagnostics: [../playwright-cli/references/tracing.md](../playwright-cli/references/tracing.md)
