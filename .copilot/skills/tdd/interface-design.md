# Interface Design for Testability

Good interfaces make testing natural:

1. **Accept dependencies, don't create them**

   ```typescript
   // Testable
   function processOrder(order, paymentGateway) {}

   // Hard to test
   function processOrder(order) {
     const gateway = new StripeGateway();
   }
   ```

2. **Return results, don't produce side effects**

   ```typescript
   // Testable
   function calculateDiscount(cart): Discount {}

   // Hard to test
   function applyDiscount(cart): void {
     cart.total -= discount;
   }
   ```

3. **Small surface area**
   - Fewer methods = fewer tests needed
   - Fewer params = simpler test setup

## UI Testability = Accessibility

For browser UI, the public interface is what the user (and assistive technology) can perceive: **roles, labels, accessible names, visible text, headings, regions, and interactive controls**. DOM structure, CSS classes, framework state, and component hierarchies are implementation details.

Design UI so semantic anchors exist for every meaningful interaction:

- Use real `<button>`, `<label>`, `<nav>`, `<main>`, headings — not unlabeled `<div>`s with click handlers
- Give every interactive element an accessible name (visible text, `aria-label`, or associated `<label>`)
- Use ARIA only when no native semantic exists

If a control can't be found by role, label, name, or visible text, it's both **harder to test and less accessible**. Designing for one improves the other. This is why UI tests driven via the [playwright-cli](../playwright-cli/SKILL.md) skill use the accessibility-tree snapshot as their public interface — see [ui-testing.md](ui-testing.md).
