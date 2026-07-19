# Research — Can Hatchling single-source the skill catalog into the wheel?

> Wayfinder research ticket: [#99 — Verify Hatchling can single-source the skill catalog into the wheel](https://github.com/bradcstevens/git-loopy/issues/99)
> Part of map [#98 — git-loopy init scaffolds the full workflow skill catalog](https://github.com/bradcstevens/git-loopy/issues/98)
> Throwaway research branch: `research/verify-hatchling`

## Verdict: **FEASIBLE** (empirically confirmed)

Single-source packaging of the skill catalog from repo-root `.copilot/skills/` — **without** duplicating trees into `git_loopy/skills/` — works via `[tool.hatch.build.targets.wheel.force-include]` with parent-relative paths (`../../.copilot/skills/<name>`). Selective inclusion (ship 26, drop 3) is achieved by listing exactly the skills to ship. **No `init.py` code change is required** for the wheel path — but there are two decision-shaping gotchas for the packaging ticket (#101), especially **editable installs**.

## Empirical proof (built here, not predicted)

Scratch copy preserving the `../../` layout (`.copilot/skills/` two levels above the pyproject), vendored `git_loopy/skills/setup-agent-skills/` deleted, `force-include` added for `grilling`, `wayfinder`, `setup-agent-skills` (and `microsoft-foundry` present in source but **not** listed):

```
$ uv build --wheel
Successfully built dist/git_loopy-0.0.1-py3-none-any.whl

$ python -c "import zipfile,glob; print([n for n in zipfile.ZipFile(glob.glob('dist/*.whl')[0]).namelist() if 'skills/' in n])"
  PRESENT  git_loopy/skills/grilling/SKILL.md
  PRESENT  git_loopy/skills/wayfinder/SKILL.md
  PRESENT  git_loopy/skills/setup-agent-skills/SKILL.md
  EXCLUDED git_loopy/skills/microsoft-foundry/   (present in source, unlisted → absent from wheel)
```

Parent-path escape ✓, selective inclusion ✓, `packages=["git_loopy"]` coexistence ✓.

## Working `pyproject.toml` snippet (26 skills)

```toml
[tool.hatch.build.targets.wheel]
packages = ["git_loopy"]

[tool.hatch.build.targets.wheel.force-include]
# 26 skills from repo-root .copilot/skills/ — paths relative to this pyproject.toml.
# Excluded: microsoft-docs, microsoft-foundry, playwright-cli.
"../../.copilot/skills/batch-grill-me"                = "git_loopy/skills/batch-grill-me"
"../../.copilot/skills/code-review"                   = "git_loopy/skills/code-review"
"../../.copilot/skills/codebase-design"               = "git_loopy/skills/codebase-design"
"../../.copilot/skills/diagnosing-bugs"               = "git_loopy/skills/diagnosing-bugs"
"../../.copilot/skills/domain-modeling"               = "git_loopy/skills/domain-modeling"
"../../.copilot/skills/find-skills"                   = "git_loopy/skills/find-skills"
"../../.copilot/skills/grill-me"                      = "git_loopy/skills/grill-me"
"../../.copilot/skills/grill-with-docs"               = "git_loopy/skills/grill-with-docs"
"../../.copilot/skills/grilling"                      = "git_loopy/skills/grilling"
"../../.copilot/skills/handoff"                       = "git_loopy/skills/handoff"
"../../.copilot/skills/implement"                     = "git_loopy/skills/implement"
"../../.copilot/skills/improve-codebase-architecture" = "git_loopy/skills/improve-codebase-architecture"
"../../.copilot/skills/intake"                        = "git_loopy/skills/intake"
"../../.copilot/skills/prototype"                     = "git_loopy/skills/prototype"
"../../.copilot/skills/research"                      = "git_loopy/skills/research"
"../../.copilot/skills/resolving-merge-conflicts"     = "git_loopy/skills/resolving-merge-conflicts"
"../../.copilot/skills/setup-agent-skills"            = "git_loopy/skills/setup-agent-skills"
"../../.copilot/skills/skill-creator"                 = "git_loopy/skills/skill-creator"
"../../.copilot/skills/tdd"                           = "git_loopy/skills/tdd"
"../../.copilot/skills/teach"                         = "git_loopy/skills/teach"
"../../.copilot/skills/to-questionnaire"              = "git_loopy/skills/to-questionnaire"
"../../.copilot/skills/to-spec"                       = "git_loopy/skills/to-spec"
"../../.copilot/skills/to-tickets"                    = "git_loopy/skills/to-tickets"
"../../.copilot/skills/triage"                        = "git_loopy/skills/triage"
"../../.copilot/skills/wayfinder"                     = "git_loopy/skills/wayfinder"
"../../.copilot/skills/writing-great-skills"          = "git_loopy/skills/writing-great-skills"
```

A single `"../../.copilot/skills" = "git_loopy/skills"` mapping would ship **all 29** — `force-include` does **not** honor the `exclude` pattern system, so specific skills are dropped only by *not listing them*. (A tiny build hook that enumerates `.copilot/skills/` minus a 3-item denylist is the DRY alternative to 26 hand-maintained lines — a #101 sub-choice.)

## Hard prerequisite

Delete the vendored `git_loopy/skills/setup-agent-skills/` (and any future vendored skills) from the source tree first. Otherwise `packages=["git_loopy"]` writes `git_loopy/skills/setup-agent-skills/SKILL.md` **and** `force-include` writes it again → `_WheelZipFile.open` raises *"A second file is being added to the wheel archive at the same path"*. The `packages` and `force-include` pipelines are independent and don't conflict once the on-disk duplicate is gone.

## Gotchas (inputs to the #101 packaging decision)

- **(a) Editable installs ignore `force-include` — the big one.** `pip install -e .` / `uv pip install -e .` add the source tree to `sys.path`; no wheel packing happens, so `force-include` is skipped. At runtime `importlib.resources.files("git_loopy") / "skills"` resolves to `…/git_loopy/skills/`, which **won't exist** if we deleted the vendored copy — so `init._packaged_skills_path().is_dir()` is `False` and **`git-loopy init` silently scaffolds no skills in a dev/editable install**. This is a genuine point in favor of **duplicate-and-guard** (keeps `git_loopy/skills/` physically present for both editable and wheel). Single-source mitigations exist but add complexity: a Unix symlink `git_loopy/skills → ../../.copilot/skills`, a custom `hatch_build.py` editable hook, or a runtime fallback in `_packaged_skills_path()` that walks up to repo-root `.copilot/skills/`.
- **(b) Sdist needs its own entries.** `force-include` under `targets.wheel` applies only to a direct wheel build. `uv build` (sdist → wheel-from-sdist) fails unless matching `[tool.hatch.build.targets.sdist.force-include]` entries put `.copilot/skills/` into the sdist. Simplest: publish with `uv build --wheel` only; otherwise add the sdist entries too.
- **(c) `packages=["git_loopy"]` is safe to keep** — independent pipeline; no conflict once the prerequisite (a) delete is done.

## Bearing on #101

Feasibility is not the deciding factor — both approaches work. The real trade the #101 grilling must weigh:
- **Duplicate-and-guard**: keeps today's convention (setup-agent-skills, PROMPT.md), editable installs Just Work, no packaging-config change — but ~26 duplicated trees in git + a two-place edit per skill change + a generalized byte-identical guard.
- **Single-source force-include**: one source of truth, no guard — but **breaks editable-install skill scaffolding** unless mitigated, needs the sdist caveat handled, and departs from the established pattern.

## Sources

- Hatchling forced-inclusion docs: https://hatch.pypa.io/latest/config/build/#forced-inclusion
- Docs source (parent-path example `"../artifacts" = "pkg"`): https://raw.githubusercontent.com/pypa/hatch/master/docs/config/build.md
- Empirical build: `uv build --wheel` on a scratch copy of `git-loopy/python` with the snippet above (this session).
