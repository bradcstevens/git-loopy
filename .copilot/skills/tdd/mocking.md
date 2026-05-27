# When to Mock

Mock at **system boundaries** only:

- External APIs (payment, email, etc.)
- Databases (sometimes - prefer test DB)
- Time/randomness
- File system (sometimes)

Don't mock:

- Your own classes/modules
- Internal collaborators
- Anything you control

## Designing for Mockability

At system boundaries, design interfaces that are easy to mock:

**1. Use dependency injection**

Pass external dependencies in rather than creating them internally:

```typescript
// Easy to mock
function processPayment(order, paymentClient) {
  return paymentClient.charge(order.total);
}

// Hard to mock
function processPayment(order) {
  const client = new StripeClient(process.env.STRIPE_KEY);
  return client.charge(order.total);
}
```

**2. Prefer SDK-style interfaces over generic fetchers**

Create specific functions for each external operation instead of one generic function with conditional logic:

```typescript
// GOOD: Each function is independently mockable
const api = {
  getUser: (id) => fetch(`/users/${id}`),
  getOrders: (userId) => fetch(`/users/${userId}/orders`),
  createOrder: (data) => fetch('/orders', { method: 'POST', body: data }),
};

// BAD: Mocking requires conditional logic inside the mock
const api = {
  fetch: (endpoint, options) => fetch(endpoint, options),
};
```

The SDK approach means:
- Each mock returns one specific shape
- No conditional logic in test setup
- Easier to see which endpoints a test exercises
- Type safety per endpoint

## Network Mocking in Browser Tests

The boundary rule still applies in the browser. Drive UI tests with the [playwright-cli](../playwright-cli/SKILL.md) skill and use `playwright-cli route` (or `page.route` in committed tests) **only at real system boundaries**:

- **Do mock** third-party services (payment, analytics, external SaaS APIs), error/timeout simulation, and rare edge cases that are hard to reproduce.
- **Don't mock your own backend.** Mocking the app's own API lets the frontend pass while the real frontend↔backend contract is broken. Use a real local/test backend, an in-memory DB, or seeded fixtures.
- **Register the route before the request fires** — late registration silently misses the call.

See [ui-testing.md](ui-testing.md) for the full browser-TDD loop, semantic locators, and flakiness guardrails.
