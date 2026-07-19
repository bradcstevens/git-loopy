# Research — Licensing obligations for vendoring the skill catalog in the git-loopy wheel

> Wayfinder research ticket: [#103 — Confirm wheel redistribution and attribution for the vendored skills](https://github.com/bradcstevens/git-loopy/issues/103)
> Part of map [#98 — git-loopy init scaffolds the full workflow skill catalog](https://github.com/bradcstevens/git-loopy/issues/98)
> Throwaway research branch: `research/skills-licensing`

## Verdict: **NEEDS ONE ACTION**

`mattpocock/skills` is **MIT-licensed** and unconditionally permits redistribution + modification in a downstream MIT wheel on PyPI — *except* the one MIT condition: Matt Pocock's copyright + permission notice must travel with every copy. That condition is **not currently satisfied by the wheel** (the repo-root `LICENSE` is Brad Stevens' only; the README credit is informal attribution, not the legal notice, and a wheel installer never reads the README). Excluding the 3 Microsoft/Playwright skills cleanly removes any obligation tied to them. None of the 26 vendored `SKILL.md` files embed per-skill copyright headers.

### Required action for the spec
> **Add a `git_loopy/THIRD_PARTY_LICENSES.txt` to the wheel** carrying Matt Pocock's verbatim MIT copyright + permission notice (the text of `https://raw.githubusercontent.com/mattpocock/skills/main/LICENSE`). Because the wheel is built with `packages = ["git_loopy"]`, a file placed under `git_loopy/` is included automatically — no extra Hatchling config. The existing README acknowledgment is good practice but does not discharge the MIT "included in all copies" condition.

## 1. Upstream license — `mattpocock/skills`

Confirmed at `https://raw.githubusercontent.com/mattpocock/skills/main/LICENSE` (HEAD `9603c1cc8118d08bc1b3bf34cf714f62178dea3b`): standard **MIT**, `Copyright (c) 2026 Matt Pocock`. The permission grant covers "use, copy, modify, merge, publish, distribute, sublicense, and/or sell" — so redistribution + modification in a downstream MIT PyPI wheel is permitted.

- No `NOTICE` file exists upstream (HTTP 404 on `.../main/NOTICE`; repo root listing carries none).
- The **sole** binding condition: *"The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software."*

## 2. Is the condition currently met? No.

| Location | Content | Satisfies MIT notice? |
|---|---|---|
| `LICENSE` (repo root) | Brad Stevens' MIT | ❌ not Matt Pocock's notice |
| `README.md` 158–163 | informal "inspired by… his skills form the foundation" | ❌ credit, not a copyright/permission notice |
| `pyproject.toml` | `license = { text = "MIT" }` (Brad Stevens') | ❌ no MP mention |
| `git_loopy/skills/**` in wheel | 26 SKILL.md, no headers | ❌ no notice embedded |

A `pip`-installed wheel exposes Brad Stevens' `LICENSE` via metadata; Matt Pocock's notice ships nowhere. Hence the required `THIRD_PARTY_LICENSES.txt`.

## 3. The 3 excluded skills — confirmed third-party, cleanly severable

- `microsoft-foundry/SKILL.md:4-6` — frontmatter `license: MIT`, `author: Microsoft` (and all sub-skills carry `license: MIT`).
- `microsoft-docs/SKILL.md` — Microsoft-authored (Microsoft Learn MCP, `@microsoft/learn-cli`, `microsoft_docs_search`).
- `playwright-cli/SKILL.md` — reference for the `playwright-cli` binary (Playwright/Microsoft).

Excluding all three from the wheel removes any obligation tied to their authorship. No residual obligation if absent.

## 4. Spot-check of the 26 — no embedded headers

Grep for `^license:`/`^author:`/`^copyright`/`SPDX` across `.copilot/skills/**/*.md` matched **only** `microsoft-foundry/**` (excluded). `grilling/SKILL.md`, `wayfinder/SKILL.md`, `to-spec/SKILL.md` frontmatter carry only `name`/`description`(/`disable-model-invocation`). No per-skill `LICENSE`/`NOTICE` files exist anywhere under `.copilot/skills/`. Nothing extra to preserve beyond the single upstream MIT notice.

## 5. Packaging mechanics (confirmed)

`init._packaged_skills_path()` → `files("git_loopy") / "skills"`; `_scaffold_skills()` copies that tree into the target `.copilot/skills/`. The wheel ships skills purely via `[tool.hatch.build.targets.wheel] packages = ["git_loopy"]` (`pyproject.toml:86-87`) — so any file under `git_loopy/` (incl. a `THIRD_PARTY_LICENSES.txt`) is wheel-packaged with no extra config.

## Citations

- Upstream MIT text — `https://raw.githubusercontent.com/mattpocock/skills/main/LICENSE` (commit `9603c1cc`)
- No upstream NOTICE — HTTP 404 `.../main/NOTICE`
- git-loopy MIT — `LICENSE:1-3`
- README credit — `README.md:156-163`
- pyproject license/build — `git-loopy/python/pyproject.toml:6, 86-87`
- Skills in wheel — `git-loopy/python/git_loopy/init.py:316-317, 326-333`
- microsoft-foundry authorship — `.copilot/skills/microsoft-foundry/SKILL.md:4-6`
- Clean 26 frontmatter — `.copilot/skills/{grilling,wayfinder,to-spec}/SKILL.md`
