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
