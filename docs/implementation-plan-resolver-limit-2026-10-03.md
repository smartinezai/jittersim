# Keep website lookups within one process limit

## Reviewer summary

PR 51 limits each individual call to eight DNS threads. A timed-out call leaves its running system lookups behind while the next call creates another pool. Five calls with different slow names can therefore keep forty lookups running. The Pi may eventually run out of threads while Apply and the scheduler continue retrying.

Keep one shared pool of eight website lookup workers for the lifetime of the process. Bound submitted work across all callers. Keep the existing ten-second plan deadline, late-result cache, same-name joining and warning text. Preserve ordinary batches larger than eight by admitting more names as earlier work finishes. No database or installer changes are needed.

This is a focused correction to the existing hardening PR. Review the summary and the review notes before production implementation. Merge only after the exact combined source passes its checks. GitHub currently refuses to start jobs because of billing. That failure provides no test evidence.

## Scope and decisions

- Source: `src/jittersim/engine/website.py` and focused tests in `tests/unit/test_website.py`.
- One process-wide executor with eight workers. Allow at most 32 unfinished tasks across the process including the eight running tasks. Queue admission must be checked under the same lock as lookup ownership.
- System lookups for the same name join the existing future. Injected resolvers used in tests must use the same capacity limits without sharing their answers across resolver identities.
- A call may admit at most eight of its own unfinished lookups at once. Refill that window as answers arrive while the original deadline still has time.
- If other callers occupy all available capacity, return best-effort results with the existing unresolved-name warning. Do not create another executor or wait outside the original deadline.
- Cancel this call's queued work when its deadline ends. A running system lookup may finish later and fill the existing bounded cache. Cancellation and callbacks must not deadlock the cache lock.
- Keep the cache limit of 512 entries and the timeout memory limit of 1024 names. Start the sixty-second cache lifetime when the lookup completes. Preserve the shared deadline across all website filters in one plan.
- Do not alter service matching, network rules or HTTP APIs in this correction.

## Verification before implementation

1. Add a regression using blocked system lookups and five successive calls with different names. Release every lookup in cleanup. Demonstrate that the old source exceeds eight active lookups. Repeat with concurrent callers and verify that a slow successful answer remains fresh from its completion.
2. Verify the existing single-call test does not catch that regression.
3. Review admission, callback and cancellation behavior for concurrent callers and completed futures.

## Implementation and final checks

1. Replace per-call executor creation with shared bounded admission. Keep future callbacks outside the state lock.
2. Add checks for concurrent callers, large fast batches, exhausted capacity, slot recovery, same-name joining and late answers.
3. Run the focused website and service-refresher suites plus runtime, plan and warning checks. Run the full Python suite and lint on the final source.
4. Bring the current integration branch forward in a separate review branch without changing another owner's worktree. Review conflicts and run the checks again if the source changes.
5. Publish one finished correction PR against PR 51. Do not force push. Attach the PR to the current chat. Wait for actual green CI before merging.
6. Keep deployment with the update owner. After the correction reaches the combined release, normal Apply and status acceptance are required on the Pi.

## Deployment and recovery

There is no new setting or stored-data format. The released package includes the corrected Python module. Rollback installs the previous signed release and preserves configuration. The old release restores the original thread-growth risk. A permanently stuck system DNS lookup cannot be killed safely by a Python thread. Shared capacity limits the damage and the existing service stop deadline remains the recovery path.

## Review notes

- A worker limit alone is insufficient. The executor queue also needs a fixed admission limit.
- Creating one shared pool for both the website layer and the service refresher would deadlock if service workers submit website work to their own pool. Keep those layers separate in this patch.
- Never call cancellation or add an already-completed future callback while holding the cache lock. Both can immediately invoke cleanup.
- A cache write must use the completion time for its expiry. A lookup that was slow for more than sixty seconds should still provide a fresh answer on completion.
- Tests must release all blocked calls before removing their socket mock. A test that leaves background work running can contaminate later tests.
- The service refresher has its own per-round pool for custom resolvers. Audit its callers separately rather than claim this patch establishes a limit for every DNS-related thread in the application.

## Open question

Human feedback on this focused repair plan is pending. The regression and source review can proceed while that feedback is pending. Production code waits for the required review checkpoint.
