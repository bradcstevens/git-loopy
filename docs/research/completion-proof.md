# Can this repository admit and drive a Completion proof?

Facts for [research ticket #758](https://github.com/bradcstevens/git-loopy/issues/758)
on [map #648](https://github.com/bradcstevens/git-loopy/issues/648).
Measured 2026-10-12. **No decision is made here, and no Completion proof was run.**

## Bottom line

- Mining yields **105 candidates**, overwhelmingly `implementation` (85), not
  105 admitted Proving tasks. There are no `planning` or `review` candidates.
- **Zero tasks were admitted in five bounded admission probes.** All five hit
  the 60-second Python-loop bound before producing a test result. The complete
  survivor count is **not established**, not zero: 100 candidates were not
  replayed. This investigation does not prove that 30 tasks are available, nor
  that the repository cannot supply them. Independently, 35 candidates have an
  offline-identifiable harness-stamp incompatibility with the supplied shared
  environment.
- Existing Trials work **one Routed pair**, not a release's per-ticket pipeline.
  The reusable parts are mining, admission, worktree replay, oracle scoring,
  gate scoring, and billing observation. Driving the stable and candidate Runs,
  recording their closure outcomes, and blind review are not what this Trial
  entry point does.
- The historical author-work reference is **1,415 median / 2,518 mean AI Credits
  per closed issue**. Reviewer-session cost is **not measured** in the Run logs.
  The ADR's 170,000–240,000-credit envelope is not evidence that the complete,
  reviewed 120-repetition experiment fits that envelope.

## Measurement boundary and artifacts

The source snapshot is `origin/main` at
[`6668f9546ec0eaeadf9c2a02f408c9f9012c8215`](https://github.com/bradcstevens/git-loopy/tree/6668f9546ec0eaeadf9c2a02f408c9f9012c8215),
including ADR-0085. The stable arm is the existing `v0.11.1` tag at
`e7aa86039956fa5611d53813a74b3b46ab1ed829`, not today's source with its version
string changed. ADR-0085 requires roughly 30 admitted tasks, at least two
repetitions per arm, fixed pins and policies, fresh histories, fail-to-pass plus
the whole gate, and blind review of both arms; it describes accepted design,
not a shipped proof harness. [Contract](../adr/0085-completion-counts-verified-closures-and-a-completion-proof-gates-the-1-0-0-cut.md#two-instruments)

Everything written was in the reserved research worktree. The 110 JSONL Run
logs in the main checkout were read only. There were no Agent sessions, live
Trials, model calls, dependency installations, or calibration searches.
Admission worktrees were nested under the reserved worktree and removed.

Artifacts are in [`completion-proof/`](completion-proof/):

| Artifact | What it records |
| --- | --- |
| `mine.py`, `mined.json` | The complete closed-backlog read, candidate pins and bodies, exclusions and per-type counts |
| `admit.py`, `admission.jsonl` | Five serial, 60-second-per-loop probes using production `ReplayVerifier` |
| `inspect_corpus.py`, `corpus-environment.json` | Each candidate's historical covering loops, fixed-tree SDK pin and CLI fixture stamp |
| `shared-sdk-stamp.txt` | The single offline test reproducing the supplied environment's rc3-versus-rc4 mismatch |
| `costs.py`, `costs.json` | Read-only Run-log aggregates, reusing the baseline extractor and quantile convention |

Python came from the main checkout's existing `.venv/bin/python`, with
`PYTHONPATH` selecting the research worktree's source. Admission did not run
`uv sync`: it substituted that interpreter for the historical loop's
`uv run ... python` launch, left the test selection unchanged, and refused
non-Python loops rather than building or downloading their dependencies.
`TMPDIR` and test scratch were inside the research worktree; package-feed
defaults remained Microsoft-managed. These are **host-specific bounded
probes**, not canonical pin-matched admission of the whole corpus.

## 1. Candidate inventory and admission

The mining read is complete: **628 closed issues**, **105 candidates**, **523
exclusions**. The live labels and issue bodies were read at measurement time;
the git history was frozen at the source snapshot.

| Task type | Mined candidates | Admission probes | Admitted in probes | Not replayed |
| --- | ---: | ---: | ---: | ---: |
| `planning` | 0 | 0 | 0 | 0 |
| `review` | 0 | 0 | 0 | 0 |
| `implementation` | 85 | 1 (#753) | 0 | 84 |
| `test` | 4 | 1 (#630) | 0 | 3 |
| `docs` | 5 | 1 (#543) | 0 | 4 |
| `chore` | 4 | 1 (#751) | 0 | 3 |
| `bugfix` | 7 | 1 (#645) | 0 | 6 |
| **Total** | **105** | **5** | **0** | **100** |

Mining requires a Task-type label, an AFK-ready executable body, exactly one
reachable closing commit, changed test paths, and an available parent.
It neither classifies missing labels nor establishes fail-to-pass.
Test-path recognition also misses Rust inline tests outside conventional
test paths. [Mining: `proving_set.py:197–223, 250–289, 340–389`][mining]

Exclusions: 394 lack a Task-type label; 49 lack a closing commit; 5 have ambiguous
closing commits; 48 lack both body sections; 12 are planning documents; 2 lack
What to build; 13 have no test change. The snapshot also lists otherwise
mineable unlabelled issues; **no issue was labelled by this research**.

Admission must apply the fix's test paths at the base, see a genuine test
failure, apply the complete historical fix, and see the same covering loops
pass. A timeout is explicitly `unrunnable`, not a test failure.
Admission is not itself the whole-repository pass-to-pass gate; the Trial runs
that separately. [Admission: `proving_admission.py:384–470, 557–581`][admission];
[oracle selection: `trial.py:129–230`][oracle];
[Trial scoring: `trial.py:632–686`][trial]

The five probes took about 61 seconds each and all timed out in the **before**
phase. They establish no fail-to-pass task. Running more whole Python suites
with this same bound on the loaded host would not establish the requested
survivor census; none of these infrastructure refusals is reported as a
task's genuine failure or as evidence of model weakness.

There is a separate environment problem even without the timeout:

- The supplied environment is SDK `1.0.17rc3` / CLI `1.0.92-3`; the source
  snapshot pins rc4 / CLI `1.0.92-4`. The isolated conformance test fails exactly
  on those stamp strings. [Test: `test_conformance.py:3545–3559`][stamp]
- The fixed trees require SDK `1.0.5` (68 candidates), `1.0.13` (2),
  `1.0.14rc1` (9), `1.0.14` (23), `1.0.17rc3` (1), and `1.0.17rc4` (2).
  That is target-test dependency history, not the proof's choice of model.
- **35** candidates select the Python suite and carry the installed-SDK stamp
  assertion with a different fixed-tree CLI stamp. Their historical fixed tree
  cannot pass that suite in this shared environment. **70** lack that specific
  blocker; this does not admit them. In particular, older stamp tests assert a
  literal captured CLI version rather than importing the installed SDK's pin.
- **100** candidates select a Python covering loop; **5** select only
  Rust/PowerShell/shell loops. This is a cross-language test-bearing corpus,
  even though the proof's Runner path is Python.

The ordinary calibration command has another size limitation: it considers a
Task type only when mining finds at least **five** candidates, then admits
newest-first only until it has five survivors for that type.
On this inventory only `implementation`, `docs`, and `bugfix` reach that
metadata threshold: **at most 15 tasks** emerge from an ordinary all-type
calibration admission walk, not 30. The uncapped `admit()` function can visit
the whole mined corpus; the proof need not inherit the command's five-per-type
search policy. [Command: `calibration_run.py:677–721, 795–825`][calibrate];
[uncapped admission: `proving_admission.py:220–259`][admission]

**Comparison with 30:** metadata supply exceeds 30, but an admitted 30-task
set is unproved. Balanced coverage of all seven Task types is impossible from
the currently labelled candidate inventory. Even perfect survival would leave
zero planning/review tasks and only four test/chore tasks.

## 2. What Trial machinery drives, and tracker-free 0.11.1

`ReplayTrialRunner` makes a worktree at the task's base, applies its oracle,
calls one `IterationSession` with the requested model and effort, runs the
oracle again, and finally runs the whole AGENTS.md gate. Its prompt forbids
commits, pushes, branches and tracker writes. Its session has a
`CalibrationScope`, deliberately not a Run/Iteration scope, and does not enter
the Orchestrator's Strike/closure lifecycle. There is no Runner-release input,
Review-stage invocation, blind-review result, or Orchestrator-closure outcome
in this path. [Prompt/session: `trial.py:389–412, 704–765`][trial]

`calibration_run._live_harness` starts one client, constructs this same runner
with `_CalibrationSessionConfig` (permissive deny-list defaults rather than
the operator's Run Config), and feeds it pair-search requests. Swapping the
Routed pair does **not** swap the release implementation.
[`calibration_run.py:1057–1135`][calibrate]

ADR-0072's independent Review stage, fresh-Agent remediation, Review ref and
Orchestrator-owned closure are accepted design. Neither the current
`IterationSession.create_session` call nor this Trial call installs the new
Agent profiles or invokes that Review stage. The available logs contain **no
`wrapper.review.*` Events**. These are not features that a Completion proof can
exercise merely by running today's calibration command.
[Review contract](../adr/0072-independent-review-gates-closure.md);
[`session.py:730–746`][session]

The stable Runner already has a tracker-free work source:

1. Select **`GIT_LOOPY_ISSUE_SOURCE=prds`** (the prompt's abbreviated
   `ISSUE_SOURCE` spelling is not the environment resolver).
   Put one AFK-ready body in `prds/<feature>/<NNN>-task.md`; the documented
   layout includes a sibling `prd.md`. Discovery requires both body sections,
   ignores archived `done/` files and rejects symlinks.
   The replay's issue file must be tracked for the prescribed `git mv`;
   this repository's `.gitignore` excludes `prds/`, so merely writing a file
   into this checkout does not supply a movable tracked task.
   [Stable CLI: `cli.py:69–71, 2142–2152`][stable-cli];
   [stable source: `sources.py:1776–1927`][stable-source];
   [stable ignore rule: `.gitignore:47–51`][stable-ignore]
2. It still needs Git, the authenticated Copilot harness, a runnable AGENTS.md
   table, prepared target dependencies, the installed pinned Skill catalog and
   an effective Skill policy. It does **not** need `gh`, GitHub repository
   authentication or tracker labels at environment preflight.
   [Stable preflight: `run_environment_preflight.py:74–93`][stable-preflight];
   [stable Skill setup: `loop.py:8278–8350`][stable-loop]
3. Local files have no persisted Task-type labels. Classification can still
   spend a model call and attach a transient label even though its tracker
   write is refused. A driver must account for that rather than assume local
   markdown automatically freezes the mined Task type. Explicit
   `--model`/`--reasoning-effort` overrides suppress label-based routing and can
   supply the per-task frozen pair; they do not by themselves turn the
   classifier off. [Stable routing: `config.py:1049–1053, 1123–1131`][stable-config];
   [classifier: `task_type_pickup.py:119–170, 199–217`][classifier];
   [no tracker: `task_type_writer.py:125–136, 351–364`][writer]
4. The stable prompt's local completion signal is the **Agent's `git mv` into
   `done/`**, committed with the work. `PrdsIssueSource.handle_completions()`
   always returns `[]`; local readiness is always ready and the source has no
   comment channel. Consequently local markdown can drive author work, but
   its archive move is **not** a verified Orchestrator closure.
   [Stable completion: `sources.py:1804–1816, 2025–2053`][stable-source]
5. Use isolated replay repositories, not a branch with an upstream to this live
   project: the stable Run's auto-push is independent of issue source. The
   source bypasses GitHub lease construction but does not bypass publication.
   [Stable lease/push: `loop.py:592–625, 3817–3858`][stable-loop]

This does not establish a launch-ready, tracker-free paired harness. It
establishes which stable pieces exist and which observation/source boundaries
are missing. Fresh sessions are also not proof of memory/history being off:
the shared client constructor uses the operator's `COPILOT_HOME`/`HOME` data
root and sets no memory-disable option, while the session call sets no
history-disable option. Pinning and isolation must cover that boundary too.
[`copilot_client.py:21–61`][client]; [`session.py:730–746`][session]

## 3. AI Credits per repetition and review accounting

The read-only extract exactly matches the committed baseline's `runs.json`
after normalizing Python tuples to JSON arrays. The sample is still **110
logs**, 2026-07-18 through 2026-10-02, not a new population of Runs.
No new tracker-cost attribution was substituted for the baseline.

| Historical measure | n | Median | p90 | Mean |
| --- | ---: | ---: | ---: | ---: |
| Closed issue, all its Pickups through its first Run close | 171 | 1,415.4 | 4,770.0 | 2,517.5 |
| Pickup with recorded credits | 266 | 1,183.4 | 3,988.8 | 1,856.7 |
| Credited scope with exactly one `session.created` Event | 432 | 826.5 | 2,623.8 | 1,180.1 |

The first row is the established baseline, not a measurement of replay tasks.
It conditions on eventual closure and complete billing; abandoned issues are
not silently costed as zero. The baseline records **43.8%** of credited Pickup
spend on Pickups that did not close.
[`completion-baseline/baseline.txt:24–32`][baseline-cost];
[`completion-baseline/analyze.py:102–143`][baseline-analysis]

The last row is an **all-session proxy**, not a Reviewer sample: it mixes
execution and auxiliary work, routes and historical harness versions.
For example the 150 `claude-opus-5` scopes have median 1,275.4 / mean 1,518.3
credits, whereas the 113 `gpt-5.6-terra` scopes have median 498.9 / mean 829.6.
The logs contain 720 `session.created` Events and zero Review or Subagent
Events; there is no identified independent Reviewer cohort from which to
estimate its expected cost. Credits are harness-reported nano-AIU converted
to AI Credits, not token-price estimates or premium-request counts.
[`events.py:1043–1061, 1163–1203`][billing]

Let `W0`/`W1` be all author-work spend in a stable/candidate repetition,
`Rp` the candidate's total pipeline Reviewer spend, `Rm` remediation author
spend, and `Rb0`/`Rb1` the proof's blind Reviewer spend:

- **Stable repetition:** `W0 + Rb0`.
- **Candidate repetition:** `W1 + Rp + Rm + Rb1`, unless its pipeline review
  also supplies that head's qualifying blind verdict, in which case do not
  charge the same session twice.
- `Rp` is not necessarily one session: ADR-0072 allows repeated verdicts and
  fresh-Agent remediation up to handoff; changes to a reviewed head require
  another review. Removing author self-review can also change `W1`, so the
  historical `W0` is not a measured additive candidate cost.

Whether one Review verdict can satisfy both pipeline and blind-review duties
is **unspecified**, not decided here. Both arms require blind review; a budget
that adds review to the candidate only omits stable-arm proof work.
[ADR-0085 blind-review requirement](../adr/0085-completion-counts-verified-closures-and-a-completion-proof-gates-the-1-0-0-cut.md#two-instruments);
[ADR-0072 remediation](../adr/0072-independent-review-gates-closure.md)

For scale only, using the historical closed-issue median `W=1,415.4` and the
mixed-session median `R=826.5` as **assumed** session allowances, with no
remediation:

| Illustrative allowance | Credits/repetition |
| --- | ---: |
| Stable author work only | 1,415 |
| Stable plus one blind Reviewer | 2,242 |
| Candidate plus one pipeline Reviewer, blind verdict reused | 2,242 |
| Candidate plus pipeline Reviewer and a separate blind Reviewer | 3,068 |

For 30 tasks × two repetitions × two arms:

- Author work alone: **169,848** credits at the historical median, or
  **302,100** using the historical mean. Neither is a measured experiment total.
- One review session in each repetition: about **269,000** at the proxy median
  allowances, or **444,000** at the proxy mean allowances.
- An additional separate blind review for each candidate repetition: about
  **319,000** / **515,000**, respectively.

These are accounting scenarios, **not expected Reviewer cost, a reservation,
or sums of independently valid quantiles**. A sum of medians is not the median
of the paired experiment. The deterministic oracle and gate use no AI Credits;
they do consume host time. Review rejection, retries, cutoff rows, and
unreported billing must remain visible in the proof's actual record.

The required corporate-feed check also reported SDK **`1.0.17rc5`** newer than
the rc4 source pin: a prerelease finding, not a stable upgrade. Nothing was
installed or repinned. [Feed authority: `sdk_feed.py:20–30, 64–85`][feed]

## What these facts make specifiable

Without choosing an implementation, the Language paths & release PRD can now
specify the harness's boundaries: uncapped admission and its recorded
infrastructure refusals; historical target-test environments separately from
the common driver/harness pins; actual release selection; tracker-free work
binding and closure observation (not an archive move); frozen Task types and
routes; memory/history isolation; both arms' blind-review accounting; retry
and handoff spend; and a record that distinguishes mined, tested, admitted,
worked, verified and reviewed counts.

Two measurements remain explicitly unavailable: a full pin-matched admission
survivor census and Reviewer-session cost. They cannot be inferred from mining
or from author-session spend. This note selects neither a corpus, an adapter,
a review-reuse rule, nor a budget, and changes no release precondition.

[mining]: https://github.com/bradcstevens/git-loopy/blob/6668f9546ec0eaeadf9c2a02f408c9f9012c8215/git-loopy/python/git_loopy/proving_set.py#L197-L389
[admission]: https://github.com/bradcstevens/git-loopy/blob/6668f9546ec0eaeadf9c2a02f408c9f9012c8215/git-loopy/python/git_loopy/proving_admission.py#L220-L581
[oracle]: https://github.com/bradcstevens/git-loopy/blob/6668f9546ec0eaeadf9c2a02f408c9f9012c8215/git-loopy/python/git_loopy/trial.py#L129-L230
[trial]: https://github.com/bradcstevens/git-loopy/blob/6668f9546ec0eaeadf9c2a02f408c9f9012c8215/git-loopy/python/git_loopy/trial.py#L389-L765
[calibrate]: https://github.com/bradcstevens/git-loopy/blob/6668f9546ec0eaeadf9c2a02f408c9f9012c8215/git-loopy/python/git_loopy/calibration_run.py#L677-L1135
[stamp]: https://github.com/bradcstevens/git-loopy/blob/6668f9546ec0eaeadf9c2a02f408c9f9012c8215/git-loopy/python/tests/test_conformance.py#L3545-L3559
[session]: https://github.com/bradcstevens/git-loopy/blob/6668f9546ec0eaeadf9c2a02f408c9f9012c8215/git-loopy/python/git_loopy/session.py#L730-L746
[client]: https://github.com/bradcstevens/git-loopy/blob/6668f9546ec0eaeadf9c2a02f408c9f9012c8215/git-loopy/python/git_loopy/copilot_client.py#L21-L61
[stable-cli]: https://github.com/bradcstevens/git-loopy/blob/e7aa86039956fa5611d53813a74b3b46ab1ed829/git-loopy/python/git_loopy/cli.py#L2142-L2152
[stable-source]: https://github.com/bradcstevens/git-loopy/blob/e7aa86039956fa5611d53813a74b3b46ab1ed829/git-loopy/python/git_loopy/sources.py#L1776-L2053
[stable-preflight]: https://github.com/bradcstevens/git-loopy/blob/e7aa86039956fa5611d53813a74b3b46ab1ed829/git-loopy/python/git_loopy/run_environment_preflight.py#L74-L93
[stable-loop]: https://github.com/bradcstevens/git-loopy/blob/e7aa86039956fa5611d53813a74b3b46ab1ed829/git-loopy/python/git_loopy/loop.py#L592
[stable-config]: https://github.com/bradcstevens/git-loopy/blob/e7aa86039956fa5611d53813a74b3b46ab1ed829/git-loopy/python/git_loopy/config.py#L1049-L1131
[stable-ignore]: https://github.com/bradcstevens/git-loopy/blob/e7aa86039956fa5611d53813a74b3b46ab1ed829/.gitignore#L47-L51
[classifier]: https://github.com/bradcstevens/git-loopy/blob/6668f9546ec0eaeadf9c2a02f408c9f9012c8215/git-loopy/python/git_loopy/task_type_pickup.py#L119-L217
[writer]: https://github.com/bradcstevens/git-loopy/blob/6668f9546ec0eaeadf9c2a02f408c9f9012c8215/git-loopy/python/git_loopy/task_type_writer.py#L125-L364
[baseline-cost]: completion-baseline/baseline.txt#L24-L32
[baseline-analysis]: completion-baseline/analyze.py#L102-L143
[billing]: https://github.com/bradcstevens/git-loopy/blob/6668f9546ec0eaeadf9c2a02f408c9f9012c8215/git-loopy/python/git_loopy/events.py#L1043-L1203
[feed]: https://github.com/bradcstevens/git-loopy/blob/6668f9546ec0eaeadf9c2a02f408c9f9012c8215/git-loopy/python/git_loopy/sdk_feed.py#L20-L85
