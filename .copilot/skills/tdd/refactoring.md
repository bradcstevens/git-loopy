# Refactor Candidates

After TDD cycle, look for:

- **Duplication** → Extract function/class
- **Long methods** → Break into private helpers (keep tests on public interface)
- **Shallow modules** → Combine or deepen
- **Feature envy** → Move logic to where data lives
- **Primitive obsession** → Introduce value objects
- **Existing code** the new code reveals as problematic
- **UI markup/CSS/framework swaps** → Semantic Playwright tests (via the [playwright-cli](../playwright-cli/SKILL.md) skill) should keep passing because they target accessible roles/labels/text rather than DOM or CSS. If a UI refactor breaks the test and no user-visible behavior changed, the test is over-coupled — fix the test first (see [ui-testing.md](ui-testing.md))
