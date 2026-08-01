# The Skill catalog's source of record

> Where git-loopy's workflow **Skill catalog** comes from, how a maintainer
> acquires and proves the exact revision it stands behind, and why none of that
> happens during a **Run**.

Read this when you are refreshing the catalog, auditing what a released wheel
redistributes, or answering "which Skills did git-loopy 0.2.0 actually ship?".
The decision behind it is
[ADR-0023](adr/0023-pinned-external-skill-catalog.md); which Skills a Run may
*load* is a separate question, answered by
[`docs/skill-policy.md`](skill-policy.md).

---

## Three layers, one direction

```
bradcstevens/git-loopy-skills @ the pinned revision   source of record
    │  python -m git_loopy.skill_source   (explicit, maintainer-run, networked)
    ▼
.copilot/skills/                                     canonical, human-edited
    │  scripts/sync_skills.py             (explicit, maintainer-run, offline)
    ▼
git_loopy/skills/                                    packaged fallback, in the wheel
```

| Layer | Lives in | Reached |
| --- | --- | --- |
| **External catalog** — the source of record | [`bradcstevens/git-loopy-skills`](https://github.com/bradcstevens/git-loopy-skills) at the pinned revision | only by the explicit maintainer command below |
| **Packaged fallback** | `git_loopy/skills/` inside the built wheel | from disk, offline, at Run time |
| **Consumer project Skills** | `<repo>/.copilot/skills/` in *your* project | from disk, and they win over the fallback (ADR-0015) |

**A Run never reaches the network for a Skill.** Every Iteration resolves its
Skills from the packaged fallback and the consuming project's own tree. An
offline operator, an air-gapped runner, and an unreachable upstream are all
non-events. The upstream repository is load-bearing for *maintenance* only.

Your project's `.copilot/skills/` is outside this boundary entirely: the pin
governs what git-loopy redistributes, never what your repository is allowed to
hold.

---

## The pin

`git-loopy/python/git_loopy/skill_source.json` is the whole record:

```json
{
  "schema_version": 1,
  "repository": "bradcstevens/git-loopy-skills",
  "url": "https://github.com/bradcstevens/git-loopy-skills.git",
  "revision": "<full 40-character commit SHA>",
  "skills_directory": "skills",
  "license": {
    "spdx_id": "MIT",
    "path": "LICENSE",
    "sha256": "<digest of the exact notice redistributed>",
    "required_text": ["..."]
  },
  "provenance_paths": ["README.md"]
}
```

- The revision is **always a full 40-character commit SHA**. A branch, a tag, or
  an abbreviated SHA is refused when the pin is read, so no maintenance or
  Release path can rest on a floating ref.
- The licence is pinned by **digest as well as by marker text**. The markers make
  a failure readable; the digest is what decides, so a truncated or materially
  altered notice cannot pass by keeping a few familiar lines.
- The pin **ships inside the wheel**, beside `THIRD_PARTY_LICENSES.txt`. A
  released artifact can always say which upstream revision its catalog was cut
  from, with no source checkout and no network.

---

## The one command

```bash
uv run --project git-loopy/python python -m git_loopy.skill_source
```

It fetches the pinned commit SHA itself — not a ref that points at it today — into
`.git-loopy/skill-source/` (gitignored; override with `--into`), then refuses it
unless it proves out. On success:

```
acquired bradcstevens/git-loopy-skills @ 9f2222f… into .git-loopy/skill-source
32 Skills, licence MIT (LICENSE), provenance README.md
packaged fallback: 25 shared, 7 upstream-only, 0 packaged-only
```

| Flag | Use |
| --- | --- |
| `--into <dir>` | check the revision out somewhere else (re-running is idempotent) |
| `--offline` | re-validate an existing acquisition without contacting the remote |
| `--packaged-skills <dir>` | compare against a fallback other than the installed one |

### What it refuses, and why each is its own failure

| Failure | Means |
| --- | --- |
| **Wrong revision** | the checkout is at some other commit, or is at the pinned one while holding content that revision does not — including files the upstream `.gitignore` hides — so what would be validated is not the pinned content |
| **Unprovable revision** | the directory is not a git checkout at all, so it cannot prove anything. "We could not check" never reads as "it checked out" |
| **Invalid Skill layout** | a loose file in `skills/`, a directory without `SKILL.md`, unreadable metadata, or a canonical name that disagrees with its directory |
| **Missing licence/provenance** | the licence or a declared provenance file is absent, empty, or is not byte-for-byte the notice the pin's digest names |
| **A symlink out of the checkout** | `skills/`, a `SKILL.md`, the licence, or a provenance file points somewhere on the validating host. A symlink is ordinary committable content, so a checkout can be clean, at the pinned revision, and still be describing someone else's directory |

Acquisition itself refuses to write anywhere it does not own: the destination
must be empty or a previous acquisition of this command (marked inside its own
`.git`). It force-checks-out the pinned revision, so pointing it at a repository
you were working in would otherwise discard your work.

The last line of output states the boundary. Skills the upstream has and the
wheel does not (`upstream-only`) are **normal** — `SKILL_DENYLIST` in
`scripts/sync_skills.py` excludes the optional tool/vendor integrations
deliberately. A `packaged-only` Skill is the interesting direction: the wheel
ships something the source of record does not have, and the command warns about
it.

---

## Refreshing the catalog

1. **Move the pin.** Edit `revision` in `git_loopy/skill_source.json` to the
   upstream commit you reviewed.
2. **Acquire and validate it**: `uv run --project git-loopy/python python -m
   git_loopy.skill_source`.
3. **Update the canonical tree.** Copy the Skills you are adopting from
   `.git-loopy/skill-source/skills/` into the repo-root `.copilot/skills/`.
4. **Regenerate the packaged fallback**: `uv run --project git-loopy/python
   python git-loopy/python/scripts/sync_skills.py`.
5. **Commit the pin bump together with the catalog diff it justifies**, so the
   review sees the claim and the content in one place.

Steps 1–2 are how the claim in
[`THIRD_PARTY_LICENSES.txt`](../git-loopy/python/git_loopy/THIRD_PARTY_LICENSES.txt)
stays checkable rather than asserted: it names this pin instead of repeating a
revision that could drift away from it.
