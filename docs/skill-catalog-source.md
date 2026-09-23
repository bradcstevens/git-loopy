# The Skill catalog's source of record

> Where git-loopy's workflow **Skill catalog** comes from, how it gets onto your
> machine, and how a maintainer proves the exact revision git-loopy stands
> behind.

Read this when you are refreshing the catalog, auditing what an installation
obtains, or answering "which Skills did that Run actually use?". The decisions
behind it are [ADR-0023](adr/0023-pinned-external-skill-catalog.md) (the pin and
its validation), [ADR-0025](adr/0025-installed-skill-catalog.md) (installing
instead of shipping), and
[ADR-0064](adr/0064-a-paired-skill-change-is-published-proved-then-pinned.md)
(a paired change is published, proved, then pinned). Which Skills a Run may
*load* is a separate question, answered by [`docs/skill-policy.md`](skill-policy.md).

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
acquired bradcstevens/git-loopy-skills @ 75e01c4f62d002bd0a0773b3c253b61ca065f87a into .git-loopy/skill-source
43 Skills, licence MIT (LICENSE), provenance README.md
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

## Judging a candidate before the pin moves

CI proves the *pinned* revision. A Skill edit authored in a clone of
[`bradcstevens/git-loopy-skills`](https://github.com/bradcstevens/git-loopy-skills)
is invisible to that proof until someone bumps the pin — and the pin bump is
what every installation refreshes from on the next Run. Judge the candidate
first, without publishing anything and without moving the pin:

```bash
uv run --project git-loopy/python python -m git_loopy.skill_candidate /path/to/git-loopy-skills
```

The path is a checkout of the Skills repository. A working clone is judged as
it stands on disk, **uncommitted** edits included; nothing has to be committed,
pushed, or published, and a clone that has never been pushed is fine. The check
reads that directory. It reaches no network, and it does not edit the pin or
any tracked file.

A pass means the same three proofs CI runs against the pinned catalog pass
against this checkout. It is not a promise about the pin. The pin still moves
only as a reviewed edit to `skill_source.json`.

| Proof | A failure names |
| --- | --- |
| **Required Skills** | the Required Skill the candidate does not carry (`Required Skill absent: tdd`) |
| **Skill references** | the front-door reference that no longer resolves (`Skill reference does not resolve: grill-me`) |
| **Label vocabulary** | the provisioned label the candidate's `setup-git-loopy-skills/triage-labels.md` stopped providing (`Label vocabulary stopped providing: ready-for-agent`) |

A dropped triage-label row is a failure even when the canonical default would
have filled that label back in. The template is what a consumer repository
receives; the fallback is not the candidate providing the label.

| Failure | Means |
| --- | --- |
| **path does not exist** | name a directory. Nothing is fetched to fill it in |
| **not a Skills checkout** | the directory has no `skills/` catalog of Skill directories |

This is not acquisition. `python -m git_loopy.skill_source` still proves that a
checkout *is* the pinned revision, with its licence and layout. The candidate
check does not, and must not: a candidate is allowed to be a different
revision, and allowed to be dirty.

---

## Refreshing the catalog

The order is a requirement ([ADR-0064](adr/0064-a-paired-skill-change-is-published-proved-then-pinned.md)).
A Skill prompt is authored in
[`bradcstevens/git-loopy-skills`](https://github.com/bradcstevens/git-loopy-skills)
and mirrored nowhere. A working clone is expected as a sibling of this
checkout, `../git-loopy-skills`. The pin moves last. A pin that moves before
the proof is the failure this sequence exists to prevent.

1. **Publish** the Skill change upstream. Commit it in the sibling clone and
   push it, so the change is a full 40-character SHA. An uncommitted tree is
   not a published revision. There is no catalog in this checkout to edit.
2. **Preview, if you want, without moving the pin.** `uv run --project
   git-loopy/python python -m git_loopy.skill_candidate <clone>` reads the
   clone as it stands, including uncommitted edits, and is not a promise
   about the pin. That preview does not authorize the pin move.
3. **Prove the published revision.** Run the same command against a checkout
   of the SHA you published, clean of later edits. A pass still does not move
   the pin. `python -m git_loopy.skill_source` cannot stand in for this step:
   it fetches the pin, so it cannot judge a revision the pin does not yet name.
4. **Pin last.** Only after that proof passes, edit `skill_source.json` to
   that SHA. The git-loopy commit names the upstream revision and the Skill
   edit it carries. In that same change, reconcile the pin's consumers: the
   offline revision and Skill-name snapshot in `tests/test_prompt.py`, the
   README's catalog table, and both `PROMPT.md` copies when a Skill is added,
   renamed, or retired. Decide explicitly whether new Skills belong in an
   autonomous Iteration; user-invoked operations must not become implicit
   follow-up work. Keep the Required Skills unchanged unless the Run contract
   itself is changing. Then run the Python feedback loop from `AGENTS.md`.
   Acquiring the new pin so the live-catalog guards run rather than skip is
   confirmation after the pin move, not a substitute for the proof above.

Every operator picks the new catalog up on their next Run. There is no vendored
catalog to regenerate: the claim in
[`THIRD_PARTY_LICENSES.txt`](../git-loopy/python/git_loopy/THIRD_PARTY_LICENSES.txt)
names this pin rather than repeating a revision that could drift away from it.

### Upgrading existing policies

The current catalog includes 43 Skills, adding `build-iterated-agentic-loop`,
`design-control-loop`, `loose-ends`, `model-fit`, `narrow-react-prop-types`,
`release`, and `show-me`, and retiring `writing-great-skills` in favor of
`writing-for-agents`. Its workflow guidance also updates `/next`, `/handoff`,
and related planning Skills; an upgrade adopts those upstream instructions,
not just the new names.

The installed catalog is replaced wholesale, but saved Skill policies are not
rewritten. If a policy still enables `writing-great-skills`, use
`git-loopy skills edit --project` or `--global` for that scope to remove the
retired name and select `writing-for-agents` only if wanted. Check custom
`PROMPT.md` files for the retired name too. See
[Skill-policy troubleshooting](skill-policy.md#troubleshooting) for missing-name
diagnostics.

New catalog members do not automatically enter an existing allowlist. The
packaged Required Skills remain unchanged, and the packaged prompt keeps
`release`, `loose-ends`, and `model-fit` out of autonomous Iterations. Install
the catalog into your agent client separately to invoke these yourself; doing
so neither changes git-loopy's saved policy nor authorizes a release.
