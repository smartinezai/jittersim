# Bound unfinished browser DNS lookups

## Review summary

The browser proxy limits connections but unfinished DNS work can outlive those connections. When DNS is stalled repeated timed-out CONNECT requests keep adding work to an unbounded executor queue. Bound the actual unfinished work across requests and proxy restarts. Preserve the existing public-address checks, traffic marking, access rules and plan-change checks.

Reuse the reviewed shared bounded lookup helper from the current release foundation if its lifetime fits the proxy. That helper keeps a cancelled queued item counted until a worker physically consumes it. Merely cancelling a future and freeing a slot would allow the old executor queue to keep growing.

This is a medium-risk reliability change at the browser boundary. DNS exhaustion must return the existing temporary-failure response within the current wait limit. It must never bypass address or access checks. No saved data or installation setting changes. The release coordinator assigned this repair under the current 24-hour takeover authorization and owns final publication and physical acceptance.

## Source evidence

- Reviewed foundation: `8d871a7450e4a104985afd7c7b3994964bf75889`. Before source edits inherit `6bf1bec98c139dbcc5bdd71b21854ff064279e26`, which adds the reviewed helper response-lock repair.
- Existing `BrowserProxyServer` creates eight DNS workers with an unbounded executor queue.
- The request handler waits five seconds for a lookup without retaining or cancelling it.
- The connection slot is released after timeout while lookup work remains.
- A real-socket probe with eight stalled workers retained 8, 24 and 40 queued lookups after 16, 32 and 48 CONNECT requests. This behavior predates PR50 and survives the separate website/service resolver repair.

## Steps

1. Have a separate reviewer check the admission limit, physical cancellation and lifetime before source edits.
2. Add deterministic regression coverage with blocked resolver work. Repeated timed-out requests and closing/recreating proxy servers must not increase workers or physically queued tasks beyond the selected fixed limit. Capture failure on the old source.
3. Reuse `engine/lookup_pool.py` or a minimal shared lifetime wrapper. Bound running and physically queued work together. Retain each submitted result so timeout can cancel work that has not started without releasing physical queue capacity early.
4. Refuse admission when capacity is exhausted. Return the existing HTTP 503 failure and keep the five-second request deadline.
5. Review server closure and concurrent requests. Cancel queued work belonging to a closed server without stopping a pool used by another server. Catch cancelled lookup results and produce a controlled 503 or clean shutdown with no traceback or upstream connection. A close racing with submission must reject the request or track and cancel its result. Attach completion callbacks and cancel results outside the tracking lock because completed callbacks can run immediately. Running system lookups cannot be forcibly stopped and must remain counted until they finish.
6. Test recovery after the blocked resolver is released, closure during a pending request and repeated proxy recreation. Confirm public IPv4/IPv6 filtering, marking failures, access changes and plan generation checks using existing tests.
7. Run affected proxy and lookup-pool tests, lint and independent source review. Complete local and combined Linux/browser verification before the coordinator publishes the final candidate.

## Bounds and failure behavior

Use at most eight DNS workers and the foundation helper's fixed maximum of 32 unfinished physical items. Keep the existing 32 connection slots and header, timeout and tunnel limits. Share capacity across proxy server instances in one process so restarts cannot multiply stuck workers. Do not introduce request retries. Bound any per-server tracking by the global unfinished-work limit and remove completed results.

## Security and data

This change must retain hostname validation, public destination filtering, IPv4/IPv6 address handling, socket marking, protected clients, live household access checks and rejection after relevant plan/access changes. No endpoint or permission change is introduced. No extra hostname history, credentials or request contents are retained or logged. Tracking lasts only for admitted unfinished work.

## Deployment and recovery

No migration, backfill, new dependency or special setup is needed. The ordinary official release carries this change. The coordinator retains management connectivity and rollback through the previous release. No Pi action is part of this isolated repair.

## Verification record

Independent plan review approved the shared bounded pool design and required explicit cancellation handling, close/submission race coverage and callbacks outside the tracking lock. These requirements are included above. Record deterministic before/after limits, recovery and closure results, affected tests, independent source review and the focused source commit before handoff.


Completed source repair: `894d40ec346c20e7c2b933dc158a6c47e51f0fe8`.

- Both baseline socket checks failed before repair. Repeated requests retained eight running plus 40 queued lookups. Three server recreations multiplied stalled workers to 24.
- Seven new socket regressions passed after repair. They cover physical queue bounds, shared worker bounds, recovery without running cancelled queued work, queued cancellation, closure racing with submission, completion after closure and immediate completion callbacks.
- The combined affected run passed 128 tests. Full required Ruff and whitespace checks passed.
- Independent source review found no blocker in admission, physical cancellation capacity, callback lock ordering, closure races or preserved address/access/marking/generation checks.
- Final combined source passed 3,071 tests with 39 expected skips on both Mac and Linux. Fresh installed-wheel general-profile, protected-client and complete browser journeys passed.
- The service-list packet check passed with IPv4, IPv6, rerolls, outages and protected clients.
- Image `jittersim-pr50-proxy-final` has identifier `4069c3e0469d3b21d8d4963c81fc75151e7bb93b7c4112a1086e858961788aef`. All 251 relevant tracked source, test and deployment files matched the final source. Old source/test/deployment folders were removed inside the build before copying the final tree and reinstalling its wheel.

The Docker slot was released after the checks. The release coordinator owns final combined checks and publication. PR72 remains open with failed CI checks. GitHub's recorded annotation says the jobs did not start due to failed account payments or a spending limit. No remote publication or device action occurred here.
