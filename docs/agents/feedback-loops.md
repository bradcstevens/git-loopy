# Feedback-loop exclusions

Notes on what is deliberately *not* in the table, because every row in it is a
blocking gate:

- **`ruff`** carries pre-existing errors, so declaring it would make Integration
  permanently red. Run `uv run --project git-loopy/python --all-extras ruff check
  git-loopy/python` on the files you touched and keep them clean; it becomes a gate
  row once the backlog is cleared.
- **The Conformance adapter subset** the CI runs as its own fast job
  (`test_conformance.py`, `test_release_identity_conformance.py`) is already
  inside the Python suite row; a second
  row would only pay for it twice.
- **The native Windows timezone proof** in `runner-family-gate.yml` repeats Rust
  format/lint checks and runs the native timezone API and executable regressions
  on Windows. Those tests are already part of the Rust suite above on a Windows
  host; another row would either repeat them there or require Windows APIs on an
  operator's non-Windows host. CI supplies that additional platform evidence.
- **`python -m git_loopy.skill_source`** (acquire and validate the pinned external
  Skill catalog, ADR-0023) reaches the network, so it can never be a gate: an
  unreachable upstream would make Integration red for a reason no change here
  caused. Its offline half — the immutable pin, the real fetch/checkout path over
  a `file://` remote, and every validation failure — is covered by
  `tests/test_skill_source.py` inside the Python suite row.
- **`git-loopy doctor --apply`** refreshes that same catalog before it repairs a
  Skill policy (#518), so it inherits the same exclusion for the same reason. Its
  offline half — the pin comparison, the `absent`/`drifted`/`matching` verdicts,
  the "refresh, do not prune" rule they impose on a missing-Skill row, and the
  refresh itself over a `file://` remote — is covered by
  `tests/test_skill_install.py` and `tests/test_doctorcmd.py` inside the Python
  suite row.
- **`python -m git_loopy.sdk_feed`** reports when the corporate Python feed
  carries a `github-copilot-sdk` release newer than the pin in the Runner
  manifest (#639). It reaches the network, so it can never be a gate: an
  unreachable feed would make Integration red for a reason no change here
  caused. Its offline half — the pin read from the manifest, and the
  comparison that reports a newer release, distinguishes a prerelease from a
  stable upgrade, and refuses to treat a feed error as no upgrade — is covered
  by `tests/test_sdk_feed.py` inside the Python suite row. Run it when working
  in this repository and surface any finding; a finding does not change the
  pin, and a feed error is never "no upgrade available".
- **A paired Skill change** (ADR-0064) is authored in a sibling clone of
  `bradcstevens/git-loopy-skills`, published, proved, then pinned. It is a
  working surface, not a loop: proving it reaches that clone, and an
  unreachable upstream would make Integration red for a reason no change here
  caused. The Iteration runs that proof itself. The gate still runs from this
  repository alone.
- **The cross-repo Skills proof** in `cross-repo-skills-proof.yml` (#632) acquires
  that same pinned catalog and then runs the three Python-suite checks that read
  it — that the profiles' Required and Permitted Skills resolve at the pin,
  that Skill reference routing resolves, and that the Skill-authored label vocabulary
  matches what this repository provisions — each of which otherwise skips rather
  than fails when the catalog is not acquired or its HEAD differs from the pin.
  A stale-acquisition skip names both revisions and the re-acquire command;
  an unprovable revision, dirty working tree at the pin, or invalid pinned
  content still fails. These guards never refresh the acquisition themselves.
  It inherits the same network
  exclusion as `python -m git_loopy.skill_source` above for the same reason: an
  unreachable `bradcstevens/git-loopy-skills` would make Integration red for a
  reason no change here caused. CI supplies this as a required job precisely
  because Integration cannot, the same way it supplies the native Windows
  timezone proof above; the three checks stay offline-safe skips everywhere
  else, and the job's own shape is covered by
  `tests/test_cross_repo_skills_proof_workflow.py` inside the Python suite row.
- **`python -m git_loopy.release_rehearsal`** (prove a promoted stable snapshot
  before any public tag exists, ADR-0059) *runs this table* over its candidate,
  so declaring it as a row would make Integration gate itself recursively. It is
  release proof rather than a Lane gate: `release-promotion.yml` runs it per
  candidate and creates no tag without it, and the boundary itself — both
  triggers, every drift and refusal, the publication-input binding — is covered
  offline by `tests/test_release_rehearsal.py` inside the Python suite row.
