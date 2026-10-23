# A resumed shell path is delivered outside Runs and activates in one approved change

**Status:** accepted design. Planning only: nothing here creates a ticket, activates the shell
manifest, edits the Conformance register or makes an issue agent-ready.

Decided by [Decide the shell resumption delivery and activation gates](https://github.com/bradcstevens/git-loopy/issues/985)
on [Wayfinder: resume the shell Language path](https://github.com/bradcstevens/git-loopy/issues/979).
Builds on [ADR-0091](0091-a-dormant-path-stands-at-the-last-stable-family-release-carrying-its-owed-debt-frozen.md),
[ADR-0102](0102-a-resumed-shell-path-declares-contract-support-2-38-serial-and-without-config-parity.md),
[ADR-0103](0103-the-resumed-shell-path-speaks-headless-json-rpc-from-bash.md),
[ADR-0104](0104-the-resumed-shell-charges-strikes-per-issue-from-its-endings-and-retires-its-run-wide-claim.md)
and [ADR-0105](0105-a-resumed-shell-path-cuts-its-own-releases-and-records-the-helper-release-it-consumes.md).
Amends ADR-0091's scheduling of carried debt, and settles the live-proof re-runs that ADR-0105's
decision 10 left to the activation gates.

ADR-0102 to ADR-0105 settled what a resumed shell path supports, how it reaches Copilot, how it
charges **Strikes** and how it releases. None said who builds it, in what order, or when its
**Path manifest** may change. The path stays a **Dormant path** until it proves ADR-0102's floor,
and ADR-0091 keeps a dormant path's issues out of every **Run**, so the resumption cannot be Run
work. Two facts make the order matter. Today a Run binds any unblocked `ready-for-agent` issue,
because the `dormant_path` exclusion (#798) is not built, and its **Pickup** writes a Python
`vX.Y.Z` **Release target** that would advance Python's **Release line**. And ADR-0103's five
runtime proofs, the riskiest unknowns, decide whether the rest is worth building.

## Decision

1. **Delivery happens outside Runs while the path is dormant.** The **Loop engineer**'s approval
   of the **Planner**'s graph for the shell Spec authorizes delivery; charting it does not. Every
   `path:shell` slice before **Path activation** stays `ready-for-human`, including the carried-debt
   trackers #834 and #553 and the human tasks. A human launches each one in a fresh worktree, and it
   merges by pull request under the full gate. The graph proposes `ready-for-agent` only for its two
   Python prerequisites. A `path:shell` ticket reaches the agent-ready queue only after Path
   activation merges, through ordinary Planner approval. ADR-0091 applies as written, except that
   the carried debt is scheduled by that approval and delivered while the path is dormant, not
   "only when the path resumes". The path resumes at its Path activation.
2. **The session adapter is proved before anything else is built.** The first slice is a tracer
   bullet: one **Iteration** over one `copilot --headless` server, with the handshake gate, a
   memory-off session, the sole permission handler, teardown, an offline scripted-server suite and
   §4's interface-neutral wording. History denial (§18) follows, then the **Sandbox** (§19 and
   `wrapper.sandbox.resolved`), which shares the permission handler. A human task then runs
   ADR-0103's five runtime proofs against that production adapter. A failure keeps the path dormant
   under ADR-0102's floor, blocks every later slice, and opens a decision issue as their blocker
   (ADR-0100). Only after the proofs pass do the **Endings** (#834), then the **Attempt lifecycle**
   (#553), Host yield, verified close after publish, the fifth **Pool exclusion** reason, the
   shell-owned prompt and the Release slices (identity, the installer's **Consumed Release** and
   `shell/v*` publication) begin. No slice is parallel-safe. The Planner may split a slice, but
   never move work across the proof gate.
3. **The two prerequisites in Python are Python's own tickets.** Under ADR-0073's split rule, both
   carry `path:python`. In the first, Pickup infers no Release target for an issue owned by a
   non-Python path, and the Release-line advance refuses a `vX.Y.Z` label on one. It reads
   ownership through #793's loader and blocks Path activation. In the second, source-release
   verification follows `release_authority` and accepts `git-loopy <key> X.Y.Z` from a non-Python
   entry point. It lands before the shell's identity slice changes that output, so no gate goes red.
   #793's loader likewise follows `release_authority`, and a paid carried waiver, now `claimed`,
   satisfies its carried-waiver rule.
4. **A claim lands with the slice that proves it.** A slice flips the register row it proves, even
   above the declared **Contract support**, as ADR-0104's decision 6 already does for the carried
   waivers. It does so with fixture-derived production assertions and at least one boundary case
   through `git-loopy.sh`. A slice that edits shared authority keeps every gate it reaches green in
   the same change, including Python or PowerShell tests that only restate that authority: #834
   relaxes `test_path_baselines.py` to accept a paid waiver. PowerShell cases are narrowed by
   `distributions`, never deleted, and a contract edit takes the next free minor after scanning open
   pull requests. The shell's `gate_rows` stay as they are, and new offline suites are `test-*.sh`.
   Live proofs are recorded evidence, never gate rows. Delivery waits for neither Path-gate selection
   ticket (#799, #800), because every loop runs on every change today.
5. **Live proofs are recorded, and re-run only on change.** The proof gate and a second run on the
   Path activation candidate each record the commit, the Copilot CLI version and the protocol
   version. The first record goes on its ticket; the second goes into the paths README's activation
   audit and then the `shell/v0.12.0` notes. After Path activation, a shell Release re-runs the five
   proofs only when an adapter file changed since the recorded proof commit, or the documented
   minimum Copilot CLI or protocol version rose. Otherwise its notes restate the last pass. The
   `shell/v*` rehearsal refuses notes whose proof commit predates the last adapter change.
6. **A drift audit precedes Path activation.** It runs on the Path activation candidate, comparing
   that day's **Wrapper contract** and accepted amendments with the horizon recorded on #985
   (contract 2.41 at `e730b55a`). It lists every added or changed obligation and re-checks the
   claims the contract-gap ledger marked at risk: `discriminator`, `event-schema` emission,
   `dashboard-insights`, `release-version` and `tui-artifacts`. Each finding becomes a blocking
   slice, a cited role waiver or a decision issue before Path activation. The audit is cited by
   commit from an unmerged `audit/` branch, as the contract-gap ledger was. Path activation declares
   the minor the audit covered. If the contract moves afterward, it keeps that minor, since support
   may trail the contract of the day, or re-audits the difference.
7. **Path activation is one approved change.** It may merge only when every slice in the graph is
   closed, including any the drift audit added; the audit leaves no shell obligation unmet at the
   declared minor; the five proofs passed on its candidate; inside the declared support, every shell
   register row is `claimed` or a role waiver, and none is `waived_owed` or rests on ADR-0091's
   introduction reason; Python's and the shell's gates are green on macOS and Linux; and the Loop
   engineer approves it. It changes only:
   - `paths/shell.json`: `state: active`, `contract_support` set to the audited minor,
     `event_schema_support: [1]`, `release_authority` pointed at a new `git-loopy/shell/release.json`,
     and `docs/releases/shell/**` added to `owned_files`. `baseline` is untouched.
   - That `release.json`, at 0.11.2 with no Consumed Release, so no reported identity changes.
   - The shell's `integration-gate.json` and `account-control.json` rows, restated as role waivers:
     Parallel mode only, and Python only under §19.1.
   - The `test_path_baselines.py` assertions that restate the manifest.
   - An activation audit beside the paths README's Baseline audit.
8. **The first Release is a separate human cut.** After Path activation, a release commit sets
   `release.json` to 0.12.0 with its Consumed Release, or none. Its notes at
   `docs/releases/shell/v0.12.0.md` cite the activation proof record, the rehearsal runs on that
   candidate, and a human pushes the annotated `shell/v0.12.0` tag (ADR-0105).

## Considered options

- **A `resuming` manifest state that admits the path's issues to Runs.** Rejected: it needs a third
  manifest state, amendments to ADR-0091 and ADR-0102, changes to #793 and #798, and the
  Release-target fix first, for work whose gates need a human anyway.
- **A throwaway proof spike before the adapter.** Rejected: its proof would cover code that never
  ships, while proving the production adapter costs the same session.
- **Flipping register rows above the declared support only at Path activation.** Rejected: it parts
  each claim from the change that proves it and turns Path activation into a large register edit.
  ADR-0104 already flips the carried waivers in their own slices.
- **Re-running the five proofs before every shell Release.** Rejected: ADR-0105 already rejected a
  per-Release live smoke for its premium-request cost, and a change-triggered re-run covers the same
  risk.
- **Combining Path activation with the first release commit.** Rejected: a failed rehearsal or
  helper proof would then hold Path activation, which ADR-0105 says waits for no helper.
- **Reading the `path:` label directly for the Release-target fix.** Rejected: it adds a second
  ownership reader beside #793's loader.

## Consequences

- #553 and #834 stay the carried-debt trackers. The Planner reuses them instead of creating
  duplicates, and #553's `path:shell` label still comes from #797.
- The delivery graph, the Path activation checklist and the `/to-spec` inputs live in #985's
  resolution. The Spec and the Planner's graph follow from them.
- Until #798 lands, the only guard on pre-activation slices is tracker hygiene: none of them gets
  `ready-for-agent`.
