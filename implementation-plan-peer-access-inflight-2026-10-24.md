# Share only concurrent dashboard device checks

The server currently repeats the same helper identity read for each private request. Sharing an unfinished read can reduce contention without keeping a finished authorization answer. A request that begins after an access change starts or finishes must not join an older read. Emergency checks must retain their own deadline and bypass routine sharing. This is risk class B with low household risk because it changes the HTTP authorization path but no network rule or privileged helper source.

## Source evidence and scope

- Base: detached `aa561623`.
- `ControlRequestHandler._check_device_access` calls `ControlServer.dashboard_access` for every ordinary private check. Token validation already precedes it.
- `ControlServer.dashboard_access` performs a checked helper read with no sharing.
- Access changes use `dashboard_access` operations including remove and move. Protection syncs call the helper directly from `sync_protected_access`. Those are also grant changes.
- Existing `SharedReading` retains a completed value and waits without a deadline. It is unsuitable for identity answers without changing its unrelated consumers.
- The backlog audit item 1 requires invalidation before and after grant changes. Restricting edits to the HTTP handler cannot meet that requirement.

The required narrow extension is limited to immediate per-server state, ordinary `dashboard_access` reads, access-write invalidation and protection-sync invalidation in server.py. No other production files or shared documents change. The coordinator confirmed this narrow scope before production edits on 24 October 2026.

## Proposed behavior

1. Give each server a short-held lock and a bounded map of unfinished peer reads. Use the existing threading event approach. Never retain a completed answer in server state.
2. Share only ordinary status calls with the same peer and empty extra arguments outside a deadline scope. Loopback and emergency calls keep their current paths. Calls with values use the existing unshared path.
3. Share only the validated helper read within `_checked_access`. Keep any later protection reconciliation per caller outside the shared lifetime. Remove a read from the map before publishing completion. Return independent answer dictionaries. Propagate helper errors with their existing reason and message. On a bounded waiter timeout fail closed with helper-busy.
4. Invalidate joins before and after every access-changing call including protection syncs. Clearing the map under the admission lock marks the new generation without retaining generation history. Do not wait for a reader while changing access. Prevent a finished older read from removing a newer read.
5. Bound the map by the existing worker limit. On a full map use the current direct helper read, never evict or create an unbounded waiting queue. Actual helper socket deadlines continue to bound leaders.

## Verification and review

Add a unique focused test module with deterministic events and bounded thread joins. Prove eight simultaneous callers use one helper read, different peers and servers remain isolated, completed values are never reused, denial and helper errors reach every waiter, fresh requests after revoke use a new read, overlapping writes do not join an older generation, protection sync invalidates joins, timeouts fail closed and the table stays bounded. Preserve loopback, emergency fallback and token checks with focused existing HTTP/emergency regressions.

Run only focused synthetic helper and loopback checks through the coordinator's required wrapper. No real Pi, router, scan, browser, Docker or full gates. Ask for an independent senior review before any local ready commit. No push, PR or merge.

## Deployment, rollback and data

The change uses only Python standard-library primitives already used by the server. It adds no dependency, build input, setting or migration. In-memory peer identifiers and unfinished answers live only for a request and are not logged or persisted. Reverting the change leaves no saved state. Hosted CI and physical Pi contention acceptance remain outside this bounded local task.
