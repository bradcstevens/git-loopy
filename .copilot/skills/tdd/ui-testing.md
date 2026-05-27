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
