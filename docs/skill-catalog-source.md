# The Skill catalog's source of record

> Where git-loopy's workflow **Skill catalog** comes from, how it gets onto your
> machine, and how a maintainer proves the exact revision git-loopy stands
> behind.

Read this when you are refreshing the catalog, auditing what an installation
obtains, or answering "which Skills did that Run actually use?". The decisions
behind it are [ADR-0023](adr/0023-pinned-external-skill-catalog.md) (the pin and
its validation) and [ADR-0025](adr/0025-installed-skill-catalog.md) (installing
instead of shipping). Which Skills a Run may *load* is a separate question,
answered by [`docs/skill-policy.md`](skill-policy.md).

---

## One catalog, installed

**git-loopy ships no Skills.** A distribution carries the *pin*; the Skills
themselves are installed from the pinned external repository into git-loopy's own
config scope.

```
bradcstevens/git-loopy-skills @ the pinned revision   source of record
    │  installed at `git-loopy init`, refreshed at the start of every Run
    ▼
<config-home>/git-loopy/skills/                       the installed catalog
    │
    ▼
the one Skill source git-loopy provides for itself
```

| Layer | Lives in | Reached |
| --- | --- | --- |
| **External catalog** — the source of record | [`bradcstevens/git-loopy-skills`](https://github.com/bradcstevens/git-loopy-skills) at the pinned revision | at setup, and at the start of every Run |
| **Installed catalog** | `<config-home>/git-loopy/skills/` on your machine | from disk, every Iteration |

`<config-home>` is `$XDG_CONFIG_HOME` if set, else `~/.config` — the same rule
the rest of git-loopy's global scope follows, so the catalog sits beside the
`config.toml` that governs it. Two siblings support it and are deliberately
*not* inside it, so nothing bookkeeping-shaped can be mistaken for a Skill:

| Path | What it is |
| --- | --- |
| `<config-home>/git-loopy/skills/` | the Skill root: Skills, and nothing else |
| `<config-home>/git-loopy/skill-catalog/` | the private checkout the install is cut from |
| `<config-home>/git-loopy/skill-catalog.json` | the install record: repository, revision, Skill names, tree digest |

Your project's `.copilot/skills/` is **not** read. Neither is `~/.copilot/skills/`.
A Run's Skills are the pinned catalog, so "which Skills ran" is answerable from
the pin alone — and setup never writes a Skill into your repository.

---

## When it installs, and what happens offline

| Moment | Behaviour |
| --- | --- |
| `git-loopy init` | installs the catalog **before** collecting anything, because the Skill policy you are about to choose is a choice among the installed catalog |
| the start of every Run | refreshes it from the pin, before the Skill preflight |
| already at the pinned revision, tree intact | no-op; **no connection is opened** |
| pin moved | the new revision replaces the old one **wholesale** — a Skill retired upstream is gone |
| a Skill was hand-edited | detected by digest and **repaired**; the installed catalog is git-loopy's to own, so change the pin, not the files |
| upstream unreachable, catalog installed | **warning**, and the Run continues on the installed revision, naming it and whether it is the pinned one |
| upstream unreachable, nothing installed | the Run **fails at preflight** — it has no Skills to expose, and discovering that one Iteration later is worse |
| the acquired revision fails validation | nothing is replaced; the previous install stays exactly as it was |

An install is staged beside the Skill root and swapped into place, so an
interrupted install never leaves half a catalog.

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
    "sha256": "<digest of the exact notice the catalog carries>",
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
  released artifact can always say which upstream revision it installs, with no
  source checkout and no network.

---

## Acquiring it by hand

Every install runs the validation below. This command runs it on demand, into a
scratch directory, so you can review a revision before pinning it:

```bash
uv run --project git-loopy/python python -m git_loopy.skill_source
```

It fetches the pinned commit SHA itself — not a ref that points at it today — into
`.git-loopy/skill-source/` (gitignored; override with `--into`), then refuses it
unless it proves out. On success:

```
acquired bradcstevens/git-loopy-skills @ f16cc17… into .git-loopy/skill-source
36 Skills, licence MIT (LICENSE), provenance README.md
```

| Flag | Use |
| --- | --- |
| `--into <dir>` | check the revision out somewhere else (re-running is idempotent) |
| `--offline` | re-validate an existing acquisition without contacting the remote |
| `--pin <file>` | enforce a pin other than the packaged one |

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

---

## Refreshing the catalog

1. **Change the Skills upstream**, in
   [`bradcstevens/git-loopy-skills`](https://github.com/bradcstevens/git-loopy-skills).
   That repository is the source of record; there is no catalog here to edit.
2. **Review the revision you intend to adopt**: `uv run --project
   git-loopy/python python -m git_loopy.skill_source --into /tmp/skill-review`
   after setting `revision` to it.
3. **Move the pin.** Edit `revision` in `git_loopy/skill_source.json` and commit
   that one-line change.

Every operator picks the new catalog up on their next Run. Nothing else in this
repository needs to change, which is the point: the claim in
[`THIRD_PARTY_LICENSES.txt`](../git-loopy/python/git_loopy/THIRD_PARTY_LICENSES.txt)
names this pin rather than repeating a revision that could drift away from it.

