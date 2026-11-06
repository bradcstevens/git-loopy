# Feedback-loop rules and exclusions

## Targeted Python tests

For a docstring-or-prose or test-only change, run targeted Python tests instead
of the full Python suite only when publishing through a pull request (CI runs the
full suite on every pull request with no path filters) or delivering a Lane
contribution in Parallel mode (Integration runs the whole table); a serial Run
pushes straight to the default branch and still runs the full suite. To qualify as
docstring-or-prose, compare each changed Python file against the merge-base
yourself: both versions must parse to the same `ast.dump` after removing module,
class and function docstrings, and every other changed file must be Markdown.
A test-only change qualifies when, against the merge-base, it only adds test
modules and their owned data files under `git-loopy/python/tests/`, edits existing
test modules there, or changes Markdown; it deletes no existing Python file,
touches no `conftest.py`, shared fixture or fake module, or production module,
and changes no dependency or lockfile.
Run each changed module's own `tests/test_<module>*.py`, every test file that names
a changed file (find them with
`git grep -l -F <basename> -- git-loopy/python/tests` for each changed file),
and the tracked-tree guards `tests/test_no_*.py` and `tests/test_glossary_*.py`;
Markdown-only changes run this same set too, and test-only changes add every new
and edited test module, in full, to this set. Import-time side effects or leaked
global state from new or edited tests are an accepted residual risk caught by CI
or Integration rather than locally. Record the targeted tests run and the
full-suite backstop (CI or Integration). The known flakes are
`test_release_installation.py::test_reclaims_setup_children_not_just_their_parent[cancelled-init]`
(a pseudo-terminal "Operation not permitted" error under host load),
`test_release_smoke.py::test_a_run_still_in_flight_at_the_deadline_is_never_passed`
(its fixture deadline expires under host load), and
`test_attach_command.py::test_a_dashboard_fault_restores_the_terminal`
(a deadline or teardown timeout under host load; #734 fixes it, so remove this
entry when #734 lands). When a listed test fails in a full run, rerun it alone:
if it passes, report the flake and treat that failure as green without rerunning
the full suite; an unlisted failure or one that also fails alone is a real
failure. Record each encountered known flake and its result when rerun alone;
change this list only through normal review.

### Contributor command contract

From the repository root:

```sh
uv run --project git-loopy/python --all-extras python git-loopy/python/targeted_tests.py --backstop integration --run
```

The command is source-checkout tooling, outside the distributed `git_loopy`
package. Omit `--run` for inspection; add `--json` for one JSON object on stdout.
`--backstop ci` asserts delivery through a pull request with the full Python CI
suite; `--backstop integration` asserts a Parallel Lane whose Integration runs
the full table. These are explicit contributor assertions, not inferred from
environment variables. Omit the backstop for serial direct-to-default delivery.
The command does not alter the Integration gate or rerun known flakes.

`--base <revision>` selects an explicit comparison commit. By default the command
uses the merge-base of `HEAD` and the locally available `origin/HEAD` remote
default-branch ref. It never fetches: if that ref or its history is missing,
fetch/set the remote head yourself or supply `--base`. The report records the
resolved base commit. Comparison covers the final working-tree content relative
to that base, including committed, staged, unstaged and non-ignored untracked
files; changes undone in a later layer have no net diff. Neither the index nor
the worktree is modified. Rename detection is disabled so Python deletions are
not hidden by renames. Added/deleted Python files cannot qualify as prose.

Test modules are existing regular `test_*.py` files under
`git-loopy/python/tests/`, never `conftest.py`, helper/fake Python files, JSON
baselines or unmatched glob strings. Added data qualifies conservatively only
when owned exclusively by changed test modules: a module names its basename or
the data lives under that module's extensionless path (`test_example/data.json`).
Unowned/shared data, files in `fixtures`/`fake`/`fakes` directories, dependency
manifests/locks, edits/deletions of existing data, and symlinks require the full
suite. Ownership is lexical, not a claim to discover dynamic fixture use;
keep shared fixtures out of module-owned directories.

Selections use the same own-module prefix, basename-reference and tracked-tree
guard rules for all eligible classes, and include every new/edited test module.
Results are deduplicated and sorted by repository-relative path. The report
prints verdict, reason, selected modules and exactly one evidence-record line
with change class, selected modules and backstop. JSON exposes `base`, `verdict`,
`change_class`, `reason`, `modules`, `backstop` and the same `evidence` line.
Inspection exits **0** for `targeted`, **10** for `full`, **1** for inspection
errors (stderr, no report). No changes require `full`. Disqualifications are
evaluated in path order, with prose eligibility considered before test-only;
the first reason from those failed checks is reported deterministically.

`--run` never runs a subset after a `full` verdict (exit 10). For `targeted`, it
invokes `sys.executable -m pytest` from the repository root, using the Python
member interpreter that launched this command, and propagates pytest's exit
status, including 5 for no tests collected. Empty selection fails with exit 1.
With `--json`, pytest output goes to stderr so stdout remains machine-readable.
The command inherits package configuration unchanged and acquires no dependencies;
the enclosing `uv run` follows the operator's configured package feed.

## Feedback-loop exclusions

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
- **`python -m git_loopy.adr_number`** reports the next free ADR number after
  freshly fetching `origin/main` and listing the files every open pull request
  adds (#965). It advances past the highest published or pending number, names
  the highest number on `origin/main`, and lists each pull request's added ADR
  paths. It reads no other sessions' local logs and reserves no number; run it
  again before publishing an ADR. It reaches the network, so it can never be a
  gate: an unreachable remote would make Integration red for a reason no change
  here caused. Its offline half — the fresh-main listing, paginated pull-request
  and added-file listings, the number and evidence report, and read failures
  that exit non-zero without printing a number — is covered by
  `tests/test_adr_number.py` inside the Python suite row. An incomplete or failed
  read is an error, never permission to number from stale or partial data.
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
