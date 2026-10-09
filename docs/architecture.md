# JitterSim code ownership

Keep changes close to the feature they affect. Moving code is useful when it
reduces the unrelated state a reader must understand.

## Dashboard source

`src/jittersim/api/dashboard.html` owns the page markup. `dashboard.css` owns
its base styles. `dashboard.js` owns the main browser behavior and
`dashboard-navigation.js` owns the existing navigation and card behavior.
The scripts keep their original scope and execution order.

`api/dashboard.py` assembles these resources with the existing workflow,
access, phone, reconnect and trust assets during server import. It may read packaged
presentation resources. It does not depend on the HTTP server, configuration
repository, scheduler or privileged helper.

The assembled page continues to use inline scripts and styles. Source files
are not separate public HTTP endpoints. Package-data must include every
resource used during assembly.

## HTTP and shared state

`api/server.py` owns HTTP admission, response delivery and the page's content
security policy. Its `DASHBOARD_HTML` and
`DASHBOARD_CONTENT_SECURITY_POLICY` remain the inputs used by delivery.
Existing test fixtures may replace them.

Configuration writes still use the existing repository and scheduler owners.
The helper still owns privileged networking. Preserve transaction scope,
operation order, protected-device handling and Panic's priority when moving
request handling in later changes.

## Panic time budget

Panic is the household safety stop, so the dashboard must answer it before the
browser gives up. A browser that gives up retries while the helper may still hold
the network lock. The numbers below are the only budget. The constants named here
point back to this section. `tests/unit/test_panic_time_budget.py` checks them with
a fake clock on which every wait lasts its whole allowance.

- Browser: `PANIC_REQUEST_TIMEOUT_MS` in `api/dashboard.js` is 50 s per Panic attempt.
- Dashboard route (`api/server.py`): at most 44 s. That is the device check, the helper
  client and the database allowance.
  - Device check: every helper call shares `EMERGENCY_CHECK_BUDGET_SECONDS` (8 s). This
    covers the emergency check, one protection resync and the fallback for an older
    helper. With nothing left the answer is 503, which the page retries. It never
    grants access.
  - Helper client (`HelperClient.normal_access`): `NORMAL_ACCESS_TIMEOUT_SECONDS` (31 s).
    It asks a busy helper again only during the first `NORMAL_ACCESS_BUSY_RETRY_SECONDS`
    (5 s). Every attempt therefore starts with at least `PANIC_ANSWER_SECONDS` plus one
    second left.
  - Database writes and the response: `PANIC_DATABASE_ALLOWANCE_SECONDS` (5 s). These
    are a few small SQLite writes. They are not bounded separately. Another process
    that holds the database lock can make each write wait up to SQLite's five-second
    busy timeout.
- Helper (`networking/helper.py`): one deadline `PANIC_DEADLINE_SECONDS` (12 s) after it
  takes the request. Every wait uses only what is left of it:
  - the network lock, at most 10 s;
  - the Panic stop lock for each save, at most 2 s;
  - the ad-blocking pause, at most 8 s, and it leaves `PANIC_RELEASE_RESERVE_SECONDS`
    (4 s) for the household release;
  - the household release lock, at most `RELEASE_LOCK_WAIT_SECONDS` (10 s);
  - the release's ad-blocking transition, at most 1 s on each side.

  Commands (`networking/deadlines.py`) get the same deadline as their timeout.
  Nothing that opens the network or records the result is skipped once the deadline
  has passed. Each such step is still tried with `PANIC_STEP_FLOOR_SECONDS` (0.5 s).
  Stacked, the worst case is 23 s, below `PANIC_ANSWER_SECONDS` (25 s). A step that
  ran short is reported as incomplete recovery and the owner retries Panic. The
  release never brings restrictions back. Stopping the unused filtered resolver
  after a release is only queued (`systemctl stop --no-block`), so it does not
  delay the answer. File syncs (`fsync`) have no time bound of their own.

## Change review

- Identify which existing area owns the behavior and the state it needs.
- Keep presentation code independent of networking and stored user data.
- Avoid passing a whole server object to a new feature area.
- Keep source extraction separate from changed behavior.
- Check installed resources through a fresh wheel and a clean container when
  package configuration changes.
- Preserve bounded reads, caches, requests and background work.
- Test important failure paths and shared state rather than every wrapper.
- Recheck overlapping changes before publishing or merging.

Later separation of browser features, storage and recovery needs its own plan.
Do not move control ownership merely to reduce a file's line count.
