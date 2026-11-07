# Unread Pin acceptance story map

Acceptance ticket: #999. Spec: #993. Integrated base:
`1b7209a4745cfb91191e8c318200c3824ce0e271`.

**Acceptance remains blocked by #1030:** User Story 20 does not hold on the
Lane path with speculative route preparation enabled. The passing coverage
below is retained; the failing reproduction is recorded in #1030, not hidden
behind an expected-failure marker or a disabled production collaborator.

## Agreed seams

- **Run:** `git_loopy.loop.run`, with fake tracker, git, SDK and network ports,
  a shortened poll interval and, where needed, a shortened bound or controlled
  monotonic clock. Observes bindings, trace Events, Iteration records, units,
  Run endings and diagnostics; it does not prove live service availability.
- **Notice:** the shared `unread-pin-notice.json` Event streams through Python
  `attach_to_run` and Rust `DashboardSession`/`draw_frame`. Observes exact
  notice lines, persistence and clearing; it does not exercise a real terminal.
- **Contract:** the Event-schema payload contract and Events actually written
  by the Run. Proves wire shape and vocabulary, not external consumer behavior.

Test locations used below:

- **Run tests:** [`test_loop_parallel.py`](../git-loopy/python/tests/test_loop_parallel.py).
- **Python notice tests:** [`test_unread_pin_notice.py`](../git-loopy/python/tests/test_unread_pin_notice.py).
- **Rust notice test:** [`unread_pin_notice.rs`](../git-loopy/tui/tests/unread_pin_notice.rs),
  `every_fixture_step_folds_and_draws_the_current_notice`.
- **Contract tests:** [`test_conformance.py`](../git-loopy/python/tests/test_conformance.py).
- **Run fixture test:** Run tests'
  `test_rolling_unread_pin_fixture_at_the_complete_run_seam`, driven by all six
  cases in [`rolling-unread-pin.json`](../git-loopy/conformance/rolling-unread-pin.json).

## Stories 1-26

| Story | Acceptance test or delivery evidence |
| --- | --- |
| 1. Short blip preserves precedence | Run fixture test: `lane-pin-recovers-within-bound` and `serial-pin-recovers-with-one-unread-iteration`; Pin binds before Priority and ordinary work, with no lapse. |
| 2. Persistent unread condition lapses | Run fixture test: `lane-pin-lapses-and-priority-proceeds` and `serial-pin-lapse-releases-refill-then-priority-demand`; lapse precedes the first binding. |
| 3. Lapsed Pin stays live and binds first when read | Run fixture test: `lapsed-serial-pin-still-binds-before-priority`; `test_a_lapsed_pin_read_again_binds_before_priority_serial` covers Lane precedence. |
| 4. Failed reads never spend or refuse the Pin | `test_a_partial_read_does_not_restart_the_pins_first_unread_clock` ends by binding as `pin` on both paths; `test_a_lane_pin_names_each_dynamic_read_that_failed` and `test_a_lane_pin_announces_tracker_and_readiness_holds` cover the read sources. |
| 5. Proven refusals spend the Pin | `test_a_pin_read_and_refused_after_a_hold_is_spent` covers Blocked, Awaiting merge, Leased elsewhere, refused task type and stale on both paths. `test_a_dynamic_route_refusal_spends_an_unread_pin` covers exhausted allowance, exhausted deadline and no runnable candidate on both paths. Each ends the hold as `spent`. |
| 6. One clock across partial and changing reads | `test_a_partial_read_does_not_restart_the_pins_first_unread_clock` supplies a partial read at 0.9 of the bound and proves lapse at 1.0, on both paths. `test_a_lane_pin_changing_reads_does_not_restart_the_monotonic_bound` changes Lease failure to worktree failure at 599 seconds and lapses at 600. |
| 7. Lapse is final | `test_a_lane_pin_keeps_one_clock_when_its_read_changes_after_lapse` sees `holding/lease`, `lapsed/lease`, `lapsed/worktree`, `ended/worktree`, never a renewed hold. |
| 8. Setup failure after lapse re-caches rather than refuses | The same test fails worktree creation three times after Lease recovery, then binds the original Pin after ordinary work without spending it as a refusal. |
| 9. Priority resumes first | Run fixture test: `lane-pin-lapses-and-priority-proceeds` and `lane-pin-lapse-releases-priority-serial-work`; the serial-lapse case separately preserves the existing earned refill turn before Priority serial demand. |
| 10. Only remaining unread work has ordinary semantics | `test_a_lapsed_pin_alone_follows_the_ordinary_unreadable_candidate` compares pinned and unpinned cases: serial unresolved Readiness ends `preflight_failed`; Lane work keeps retrying, recovers, binds, then ends `empty_pool`. There is no Pin-specific ending. |
| 11. Notice names Pin, read, reason and since when | Python `test_attach_replays_every_notice_transition_without_changing_the_run` and the Rust notice test both assert the shared fixture's literal lines, including UTC and fractional timestamps. |
| 12. Notice shows held-until/proceeding and persists until resolution | Python `test_every_event_folds_to_the_shared_notice` and the Rust notice test inspect every fixture step, including unrelated Events, changed reads, all three `ended` outcomes and Run end. Streaming attach prints only changed notices; historical output is not erased. |
| 13. Python and Rust wording is identical | Python streaming-attach test and Rust notice test consume the same independent expected lines from `unread-pin-notice.json`; neither derives its expected wording from the other implementation. |
| 14. One Event per transition, not per retry | `test_a_lane_pin_read_within_the_bound_announces_only_transitions`, `test_serial_readiness_retries_use_the_pool_read_without_opening_iterations` and the Run fixture test assert exact Event sequences after repeated failures. |
| 15. Ended distinguishes bound, spent and stopped | Run fixture test proves `bound`; both refusal matrices prove `spent`; `test_a_lane_pin_announces_the_end_of_an_unread_hold` and `test_a_serial_pin_announces_the_end_of_its_hold` prove `stopped`. |
| 16. Closed read vocabulary and original reason | `test_event_fixture_pins_the_unread_pin_contract` pins all eight values. The Run fixture test checks actual Events against that payload contract, including UTC, Run scope and ended-only outcomes. `test_a_lane_pin_names_each_dynamic_read_that_failed`, tracker/Readiness tests and changing-worktree tests check the source's reason. |
| 17. Serial hold costs one unit | `test_a_serial_pins_lease_outage_costs_one_unit`, `test_a_tracker_outage_in_the_pins_iteration_costs_one_unit` and the serial Run fixture cases prove repeated failures do not drain the cap. `test_a_serial_hold_that_spends_the_last_unit_does_not_poll` retains the documented cap-of-one boundary. |
| 18. The one unread Iteration reports pin_unread | Serial Run fixture cases and `test_serial_readiness_retries_use_the_pool_read_without_opening_iterations` assert exactly one `pin_unread` Iteration, then an ordinary Iteration when the Pin binds. |
| 19. Poll the failed source without retry units | `test_serial_readiness_retries_use_the_pool_read_without_opening_iterations`, `test_a_serial_pins_lease_outage_costs_one_unit` and `test_serial_route_reads_wait_without_repeating_pickup_or_spending_units` cover Pool/Readiness, Lease, evidence and capability reads. |
| 20. Selector pacing and ledger accounting | **GAP #1030.** `test_serial_route_reads_wait_without_repeating_pickup_or_spending_units[route_selector]` passes for serial: four failed calls plus one successful call, at least one poll interval apart, five attempts and 0.25 billed credits. Its Lane variant fails pacing with production preparation enabled, although binding, transitions and ledger assertions pass. |
| 21. Stop ends operator_stop and ended/stopped | `test_a_lane_pin_announces_the_end_of_an_unread_hold[stopped]` and `test_a_serial_pin_announces_the_end_of_its_hold[stopped]` assert no binding, the stopped transition and the Run's `operator_stop` outcome. |
| 22. Warnings collapse and partial-Pool warning is suppressed | `test_a_lane_pin_read_within_the_bound_announces_only_transitions`, `test_a_lane_pin_announces_tracker_and_readiness_holds`, and the serial Readiness retry test assert transition-line counts and absence of the misleading warning. |
| 23. Serial-only members have no new obligation | Scope evidence: commits `44a64bca` (#995) and `6f462e44` (#996), Wrapper contract section 3.2 and `fixture-claims.json`. Neither implementation commit changes shell or PowerShell. The hold callback is installed only for Rolling dispatch; serial-only Python retains its walk-past rule. This is a scope claim, not a new serial-only feature test. |
| 24. Contract and Conformance record the lapse and Event | `44a64bca` adds contract 2.41, the Event payload and Lane fixture; `6f462e44` adds serial cases/contract 2.42; `b150c61e` adds shared notice/contract 2.43. Run fixture test, `test_event_fixture_pins_the_unread_pin_contract`, `test_fixture_claims_manifest_accounts_for_the_conformance_directory`, and both notice consumers exercise these artifacts. |
| 25. Fixed bound, no new Config key/flag/environment variable | Evidence: ADR-0032's #647 amendment; `_PIN_UNREAD_HOLD_SECONDS = 600.0` in `loop.py`; implementation commits `44a64bca` and `6f462e44` change none of `config.py`, `settings.py` or `cli.py`. No new Config surface is introduced by this acceptance change. |
| 26. Lease fencing is unchanged | `test_a_lapsed_pin_cannot_bind_under_another_runs_lease` introduces a rival only after lapse, on both paths: only ordinary work binds and the Pin ends `spent` with a live-Lease skip. Existing `test_a_lane_whose_lease_was_stolen_publishes_nothing_and_closes_nothing` continues to guard publication fencing. |

Operator documentation is delivered by `b848eabf` (#998); it describes the hold,
lapse, one-unit serial cost and both notice surfaces.

## Sensitivity checks

Temporary in-process mutations, never committed to production, demonstrated
red results before relying on the retained green tests:

| Broken behavior | Observed rejection |
| --- | --- |
| Suppress the `ended/spent` Event | All ten tracker/Lease/task-type refusal cases and all six Dynamic-route refusal cases fail their transition assertions. |
| Reset the monotonic origin on each unread report | Both partial-read clock cases and both pinned last-candidate cases time out instead of lapsing. |
| Emit `read: unknown` | All six Run fixture cases fail the Event-schema vocabulary assertion. |
| Stop folding Unread-Pin Events in the Python client | All six streaming-attach fixture cases fail their expected-line assertion. |
| Grant a Lease which the real take operation refused | Both post-lapse rival-Lease cases detect an unexpected Pin binding. |
| Drop routing-ledger settlement | Both serial and exploratory Lane selector cases reject the missing billed-credit record. |

The Lane-selector pacing failure is **not** a mutation result. It occurs on
the unmodified integrated production code. Disabling speculative preparation
made that isolated case pass, identifying an interaction to investigate in
#1030 rather than an acceptable acceptance-test setup.

## Feedback loops

This serial delivery requires the full Python suite, not the targeted-test
exception. The requested Rust Dashboard loop is also required, even though
this ticket changes no Rust production code. Results are recorded in the
delivery commit and #999's progress comment; green retained tests do not
resolve the Story 20 blocker.
