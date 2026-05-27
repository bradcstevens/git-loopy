# Good and Bad Tests

## Good Tests

**Integration-style**: Test through real interfaces, not mocks of internal parts.

```typescript
// GOOD: Tests observable behavior
test("user can checkout with valid cart", async () => {
  const cart = createCart();
  cart.add(product);
  const result = await checkout(cart, paymentMethod);
  expect(result.status).toBe("confirmed");
});
```

Characteristics:

- Tests behavior users/callers care about
- Uses public API only
- Survives internal refactors
- Describes WHAT, not HOW
- One logical assertion per test

## Bad Tests

**Implementation-detail tests**: Coupled to internal structure.

```typescript
// BAD: Tests implementation details
test("checkout calls paymentService.process", async () => {
  const mockPayment = jest.mock(paymentService);
  await checkout(cart, payment);
  expect(mockPayment.process).toHaveBeenCalledWith(cart.total);
});
```

Red flags:

- Mocking internal collaborators
- Testing private methods
- Asserting on call counts/order
- Test breaks when refactoring without behavior change
- Test name describes HOW not WHAT
- Verifying through external means instead of interface

```typescript
// BAD: Bypasses interface to verify
test("createUser saves to database", async () => {
  await createUser({ name: "Alice" });
  const row = await db.query("SELECT * FROM users WHERE name = ?", ["Alice"]);
  expect(row).toBeDefined();
});

// GOOD: Verifies through interface
test("createUser makes user retrievable", async () => {
  const user = await createUser({ name: "Alice" });
  const retrieved = await getUser(user.id);
  expect(retrieved.name).toBe("Alice");
});
```

## UI/UX Tests

The same rules apply in the browser: assert on what the user can perceive, through the **accessibility/semantic interface**, not the DOM, CSS, or framework internals. Drive UI TDD with the [playwright-cli](../playwright-cli/SKILL.md) skill; see [ui-testing.md](ui-testing.md) for the full loop.

```ts
// GOOD: user-visible behavior through semantic locators
test("user can sign up and lands on welcome page", async ({ page }) => {
  await page.goto("/signup");
  await page.getByLabel("Email").fill("alice@example.com");
  await page.getByLabel("Password").fill("hunter2");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByRole("heading", { name: "Welcome, Alice" })).toBeVisible();
});

// BAD: coupled to DOM structure, CSS classes, or ephemeral refs
test("signup button has primary class and submit handler", async ({ page }) => {
  await page.goto("/signup");
  await expect(page.locator(".btn.btn-primary.signup-cta")).toBeVisible(); // CSS
  await page.click("div > form > button:nth-child(3)");                    // DOM path
  await page.click("e3");                                                  // ephemeral CLI ref
  expect(await page.evaluate(() => window.__authStore.state)).toBe("ok");  // framework state
});
```

Red flags in UI tests: asserting on CSS classes or DOM hierarchy, asserting on framework/component state, full accessibility-tree snapshot matching, hard-coded `e3`/`e5` snapshot refs in committed tests, arbitrary `sleep`/`waitForTimeout`, mocking your own backend instead of using a test backend.
