# Review results: device speed test (PR 121), 10 October 2026

Two reviewers (Sol GPT-6.1 and Sonnet 5.5, read-only) looked at the full diff, then at the fix round.
Opus was not used because less than 35 percent of the weekly allowance was left. Hosted CI is blocked by
billing, so checks ran locally. This PR is NOT merged: one fix round is used and the items below are open.

## Round 1 (head 45e231d)
Seven S2 problems (a busy helper turned the run into "not measured", the saved profile ignored overrides,
the target was not checked as connected, shared-queue latency claim, Finish after Protect, a stuck window
after a late Finish, service queues not mentioned) plus smaller items. All were fixed in the fix round
(commits fa09cff to 7d6fba6). Nothing in a Panic-gated file or updates.py changed.

## Round 2 (head e129757): open items
Mechanical CI failures, fixed on the branch after the review (the budget was raised on purpose: one more
confirm call and about 26 KB of script and style):
- GUI static budget, the off-origin address list (SVG namespace name), the local CI module list
  (`device_speed_test`), a 500 for a very long id (`len(value) > 19`), a wrong link in docs/user-guide.md.

Still open (second fix round needed, then one more review of the final head):
1. S2/S3. Protection can change between the final check and the insert (speed_tests.py `_end`). Recheck
   and insert in one repository transaction, helper calls outside it.
2. S2/S3. The speed test dialog stays on screen after the dashboard locks. Register a lock hook
   (`privateDataLockHooks`) that stops polling, closes and removes the dialog and clears cached results.
3. S2. Shutdown can return while sampler threads are alive (`SpeedTestManager.shutdown` never checks
   `is_alive()`, `_trim` drops handles of live workers). Keep live workers, check after join, raise
   `WorkersStillRunning`. Also give the manager its own short timeout (the 5 s server budget can be eaten
   by one slow helper read).
4. S2. Time quotas bypass the service-rule warning. Decide from the applied plan's service rules, and do
   not warn for saved rules that are not running (a device-only start excludes service lists).
5. S2. "Same as everyone" still records the saved selection as the applied profile. Use the applied plan.
6. S2. A late successful Finish response can overwrite another device's window. Check the dialog serial
   and run before applying success responses.
7. S1/S2. `Thread.start()` failure leaves a phantom running slot (pop the run in the except). The start
   retry loops can take about 49 s while the page times out at 15 s. After `changed` or `fault` the line
   still says "Speed test saved". Docs: the plan says a missing helper permits source `none` (now 503) and
   that Start returns at once (it scans Wi-Fi and reads queues first). docs/README.md lacks the plan.
   CHANGELOG has no Unreleased line. Tests miss thread-start failure, the 404 and 503 branches in the
   window, and a hung helper at shutdown.

Owner decisions D1 to D4 of the plan (provider choice, retention, export content, label text) are still open.

## Round 3 (16 and 17 October 2026)

Main was merged into the branch (merge 3efba55, conflicts in dashboard.py, the GUI budget test and
gui-stats.json). The unpushed fix commits of 10 and 14 October were already on the branch. Each
open item was checked against the code and has a test:

| Item | State | Test |
| --- | --- | --- |
| 1. Protection recheck and insert in one transaction | Fixed on 10 October | `test_protection_check_and_result_insert_share_a_repository_transaction` |
| 2. Dialog stays after the dashboard locks | Fixed on 10 October | browser journey `device_speed_test` (lock section) |
| 3. Shutdown with live samplers | Fixed on 10 October. New this round: a stuck sampler no longer stops the rest of `server_close` (the listener, workers and other owners still stop; the error is raised at the end and the repository stays open) | `test_shutdown_reports_a_sampler_blocked_in_a_helper_read`, `test_a_sampler_that_does_not_stop_still_lets_the_server_close_the_rest` |
| 4. Service rules from the applied plan, time quotas included | Fixed on 14 October | `test_applied_service_assignments_and_active_byte_or_time_quotas_match_device` |
| 5. Same as everyone records the applied profile | Fixed on 14 October. New this round: the real general plan has no source entry, so its general profile rule and queue confirm it | `test_the_shared_general_profile_uses_the_applied_scheduler_source`, `test_the_real_general_plan_confirms_the_shared_profile_and_service_rules` |
| 6. A late Finish overwrites the window | New this round: a late Finish answer or error for an older run is ignored once a newer run is shown | `test_a_late_finish_answer_does_not_end_a_run_started_after_it` |
| 7. Thread start, start timeout, end message, docs | Fixed on 10 and 14 October. New this round: docs/README.md lists the review-fixes plan and this record | `test_failed_sampler_thread_start_does_not_leave_an_active_run`, `test_start_timeout_covers_both_sequential_helper_reads`, `test_end_message_marks_saved_changed_and_fault_results_incomplete` |
| Round 1 shared-queue latency claim | New this round: the comparison warning says a shared queue round trip misses the profile delay | `test_compare_says_a_shared_queue_round_trip_misses_the_profile_delay` |

Owner decisions D1 to D4 are still open. Conservative defaults are recorded in the plan: Cloudflare
default with LibreSpeed as the alternative, no Phase 2 page, no helper change, 50 per device and
500 in total.

### Independent review (Sol GPT-6.1, high effort, read-only)

First pass on 114bbbd: one S1 and two S2.

- S1: a saved device without a MAC address was measured on the Everyone else queue. Fixed.
- S2: Same as everyone never confirmed in production (no source entry in the general plan). Fixed.
- S2: a queue reading with one direction made status and Finish fail with a KeyError. Fixed
  (`test_a_queue_reading_with_one_direction_keeps_status_and_finish_working`).

Re-check on efdefd2 confirmed the three fixes and found two older S2 problems:

- S2: the saved device's id could select a queue that does not carry the client's current
  address (the scheduler kept the address with another saved device). Fixed: the applied plan's
  mark rule for the current address decides the queue
  (`test_the_queue_that_marks_the_current_address_is_measured`).
- S2: a saved device id equal to a shared queue id was taken for that shared queue. Fixed: a
  shared queue needs its rule in the applied plan and a device queue never uses a shared id
  (`test_a_saved_device_id_like_a_shared_queue_is_not_taken_for_it`).

Main (36e0d2c) was merged in (208a38f, conflicts in server.py and gui-stats.json). A final review
of the full diff at 208a38f found two S1 and two S2. All four are fixed:

- S1: counters and profile could come from different applies (the helper already ran a newer
  plan than the scheduler had published). The start is now refused with 503 when the counters'
  impairments differ from the applied plan's queue
  (`test_counters_from_a_newer_apply_than_the_published_plan_are_refused`,
  `test_counters_of_a_queue_the_applied_plan_lacks_are_refused_to_retry`).
- S1: missing dedicated counters fell back to Everyone else, which the plan keeps the address out
  of. Now refused with 503 (`test_missing_dedicated_counters_never_fall_back_to_a_shared_queue`).
- S2: opening another device left the previous device's rows usable until its results loaded.
  They are cleared first (`test_opening_another_device_clears_the_previous_results_first`).
- S2: a dedicated queue whose saved device id is `general-profile` was not measured. The plan's
  mark rule now makes it dedicated whatever its id
  (`test_a_dedicated_queue_with_a_shared_looking_id_is_measured_as_the_device`).

These last fixes were not reviewed again. Without a readable applied plan the saved MAC address
still decides the queue, as before, and the service-rule state is then recorded as unknown.

### Local gates

Hosted CI is blocked by billing. Local results only:

On f9513ca: lint passed, unit 6573 passed and 73 skipped, gui 5 of 5 steps, browser --changed 34 of 34
steps. The results for the final head are in the PR comment of this round.

Nothing in panic_guard.py, networking/helper.py or networking/access.py changed. The kernel step
was not run because no networking file changed. This PR is not merged.
