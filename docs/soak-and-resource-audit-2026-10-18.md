# Soak and resource audit, 2026-10-18

Scope: defects that only show over time (leaks, unbounded tables, write amplification on the SD
card) and the memory and CPU budget of the services on a Raspberry Pi 3B+ (1 GB of memory, SD
card). Measured on `origin/main` at `8c3f8171` on a Mac, against the repository's own fixtures.
No Pi, router or outside network was touched: the soak target refuses every connection that
leaves the machine and recorded none.

Hosted CI is billing-blocked. Nothing here claims a hosted CI pass.

## Summary

- No leak showed in 30 minutes of real browser load or in 30 minutes at five times that load:
  open descriptors, threads, helper connections and the Python heap stayed flat, and request
  latency did not get worse.
- The database grows until its history tables reach their caps and then stays at that size.
  Every history table has a cap (table below).
- Fixed: the scheduler opened a new database connection on every one-second tick. Each open
  ran the schema script, the migrations and the history pruning: about 11,600 empty commits
  and 190,000 schema statements an hour for as long as a plan runs, and 62% of the CPU time of
  a tick.
- Fixed: the quota samplers stored an unchanged counter again every 5 s. With the rollback
  journal and full sync each such UPDATE is a journaled write with fsyncs on the SD card,
  720 an hour for each data limit, group limit or service quota while its device is quiet.
- Fixed: the browser proxy and the ad-blocking service loaded `requests` and its TLS stack
  (about 5 MB of Python heap each) only because `jittersim.networking` imports ad blocking.
- The largest remaining cost is polling: each open dashboard tab makes the helper run about
  0.6 network commands a second (`tc`, `nft`, `ip`, `iw`, and `nmcli` on the Router page).
  The proposals below share these reads between tabs. None of them needs a Panic-pinned file.
- No Panic-pinned file needs a change for anything found here.

## Method

The harness is in `tests/soak/`:

- `target.py` is the process under test. It runs the real `ControlServer` (HTTPS with a
  throwaway certificate, like the Pi), the real helper service behind a Unix socket with the
  browser fixture's fake network executor, and the real `RuntimeScheduler`, over a temporary
  SQLite file seeded with the fixture data. On the Pi the helper is a separate root process.
  Here it is a thread of the same process, so its numbers are the sum of both.
- `instrument.py` wraps `sqlite3.connect`, `open`, `os.open`, `os.replace`, `os.rename` and
  `os.fsync` in the target and books every write under the dashboard route or the thread and
  function that made it. A commit counts as "changed" when SQLite reports changed rows between
  BEGIN and COMMIT.
- `harness.py` starts the target and drives it like real browsers: several tabs poll the routes
  that `dashboard.js` polls at its own intervals (connected devices, labels, general profile and
  state every 5 s, health and the open page's jobs every 5 s, traffic every 2 s, impact every
  5 s), move to another page every two minutes and reload now and then (a reload checks a stale
  token and reads every route once, like the page after Check token). One more thread acts like
  the owner: save a profile, apply, save a device, apply, download a backup, Panic, restore a
  saved version, apply, import an export, start or finish a test session. Before the load it
  saves a data limit, a group and a group limit and applies, so the scheduler runs as on a Pi
  in use.
- Every sample (10 s by default) records resident memory, Python heap blocks and garbage
  collector objects, open descriptors and sockets, Python and process threads, CPU time, the
  database, its journal and the whole state folder, helper connections accepted and closed,
  helper calls, fake network commands and writes. The final report adds every table's row
  count at the start and the end, the writes per task per hour, the helper calls by action, the
  network commands by tool and the helper-busy waits.
- The judgement compares the median of the first tenth of the run after the warm-up with the
  median of the last tenth. It fails on growth beyond the limits (resident memory 24 MB, Python
  heap 25%, descriptors 8, threads 4, database 4 MB, state folder 8 MB), on helper connections
  left open, on any server error other than a busy read, on more than 1% of requests without an
  answer, on more than 2% busy reads, and on a p95 latency at the end above three times the
  start plus 100 ms.

macOS compresses and pages out idle memory, so resident size fell during every run (from about
60 MB to about 31 MB). It is reported but the heap blocks and the object count are the leak
signal on a Mac. On the Pi resident size is meaningful again.

### Commands

```sh
# a few seconds, part of the unit suite
python -m pytest tests/unit/test_soak_smoke.py

# what the owner runs for hours (Ctrl-C is fine, the report is written at the end)
python -m tests.soak.harness --minutes 240 --out ~/soak-$(date +%F)

# polling only, to see what writes on its own once a plan runs
python -m tests.soak.harness --minutes 30 --actions-every 0

# five times the browser load with tracemalloc, to find where memory grows
python -m tests.soak.harness --minutes 30 --browsers 6 --speed 5 --actions-every 3 --tracemalloc

# import time, memory and idle CPU of each service
python -m tests.soak.budget
```

Every limit has a `--limit-...` option. `--plain-http` serves HTTP and `--no-apply-first`
starts from a network that was never applied.

## Numbers

All runs on the same Mac (Apple Python 3.9, LibreSSL), HTTPS unless noted. "Before" ran the
code of `8c3f8171` (plain HTTP, the TLS option came later). "After" ran this branch.

| Run | Length | Load | Requests | Descriptors start, end, max | Threads start, end | Helper connections open at end | Database start, end | p95 start, end |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| before, real load | 32 min | 4 tabs, owner action every 30 s | 14,298 | 10, 10, 11 | 6, 6 | 0 of 5,335 | 393 kB, 578 kB | 2.0 ms, 1.5 ms |
| before, 5x load, tracemalloc | 30 min | 6 tabs at 5x, owner action every 3 s | 99,630 | 10, 10, 13 | 6, 6 | 0 of 23,611 | 606 kB, 1,356 kB | 5.1 ms, 4.8 ms |
| after, real load, HTTPS | 30 min | 4 tabs, owner action every 30 s | 13,252 | 11, 11, 12 | 6, 6 | 0 of 5,299 | 410 kB, 586 kB | 13.7 ms, 20.2 ms |
| after, 5x load, HTTPS | 25 min | 6 tabs at 5x, owner action every 3 s | 81,595 | 11, 11, 14 | 6, 6 | 0 of 19,992 | 614 kB, 1,237 kB | 28.8 ms, 9.8 ms |

The Python heap, sampled in the after runs: 192,781 to 197,812 allocated blocks at real load
(+2.6% in 27 minutes) and 204,011 to 208,054 at 5x load (+2%), with the garbage collector's
object count flat at about 36,500 in both. That is caches settling, not a leak: the 5x run made
six times as many requests and grew no more.

The two after runs ran at the same time on the same Mac, and the soak's throwaway certificate
is RSA and its clients never resume a TLS session. That made the HTTPS runs slower and costlier
than the Pi: the Pi's certificate is ECDSA P-256 (`localca.py`), browsers resume sessions, and
the before runs used plain HTTP. Compare latency within a run, not between runs.

The database growth in each run is the history tables filling up to their caps (settings
history reached its 20 versions, action history its 500 rows in the 5x run). tracemalloc over
the 5x run found no growing allocation site: the largest differences were a 1.3 MB read buffer
in `pathlib`, the line cache of one logged traceback and rows of requests in flight when the
snapshot was taken.

Requests: 0 server errors in all runs. 11 of 99,630 reads in the 5x run got 502 "could not
inspect connected clients": the helper refuses a client scan while an apply changes the network
(`network changed during client scan`). The page reads again on its next poll.

### History tables and their caps

| Table | Cap | Where |
| --- | --- | --- |
| `activity_log` | 500 rows | `storage.record_activity` |
| `action_history` | 500 rows | `storage.record_action` |
| `settings_history` | 20 versions | `workflows.checkpoint` |
| `test_sessions` | 50 sessions, at most 20 captures and 2 MB of captures each | `workflows.start_session` |
| `test_session_events` | 500 per session, removed with the session (foreign key cascade) | `storage.record_activity` |
| `pattern_events` | 7 days, 1,000 per session, 10,000 in all | `storage.save_pattern_events` |
| `domain_observations` | 30 days, 20,000 rows, 50 per device | `storage._prune_domain_observations` |
| `random_website_analytics` | 30 days, 50,000 rows | `storage._prune_random_website_analytics` |
| `address_moves`, `unaffected_devices`, `connected_device_labels` | fixed limits | `storage` |

The worst case is the test sessions: 50 sessions of 2 MB of captures each would be about
100 MB. A real session stores a few kB per capture. SQLite never gives freed pages back to the
file system (no `VACUUM`, no `auto_vacuum`), so the file stays at its largest size. That is a
bounded size and not a leak.

### Writes per task

From the polling-only runs after one Apply (no owner actions): the dashboard, the helper and
the scheduler wrote nothing on their own except the startup and the Apply itself, once the
scheduler fix was in. Before the fix the scheduler booked 11,609 empty commits and 188,653
schema statements an hour (`jittersim-scheduler engine/runtime.py:_tick` under `__init__`).
SQLite does not touch the file for an empty commit, so this was CPU and not SD wear. A
microbenchmark of 300 ticks on the seeded database: 1.63 ms CPU per tick with a new connection,
0.62 ms with the kept one.
In the after runs the scheduler opened its connection once per run (4 empty commits at the
first tick) and booked no schema work after that.

What still writes to the SD card on a schedule (from reading the code, the fixture has no
traffic counters):

| Writer | When | Commits an hour |
| --- | --- | --- |
| data limit, group limit, service quota usage | every 5 s while counters move (fixed: nothing while they do not) | up to 720 each |
| time quota usage | every 5 s while the quota is enabled, also when quiet (`last_sample_at` must move) | 720 each |
| traffic progression samples (`save_domain_observations`) | every 5 s while a traffic progression runs | up to 720 |
| pattern events | each reroll of a random profile (the owner sets the interval, 1 s at least) | 3,600 / interval |
| activity and action history | each Apply, Panic, save and scheduled plan change | per event |
| ad blocking settings (`/etc/jittersim/adblocking.json`) | twice for each household access change (suspend and resume), on Panic | per event |

The ad-blocking service writes its status and lease files every second, but both are in
`/run/jittersim` (tmpfs), so they cost CPU and not SD wear. JitterSim keeps no log files of its
own: the only files opened for appending are lock files, and logs go to journald, which PR 142
caps at 64 MB.

Each changed commit in the database costs more than one write: the database uses the rollback
journal with `synchronous = FULL` (checked in the soak target), so a commit creates the
journal, syncs it, writes the pages, syncs the database and removes the journal. An Apply
makes four such commits (runtime state twice, action history, activity).

## Memory and CPU budget on a Pi 3B+

`python -m tests.soak.budget` on the Mac. The resident size after import counts the shared
libraries too (on Linux they are shared between the processes), so the Python heap after import
is the better figure for what each process adds.

| Service | Import time (Mac) | Resident after import (Mac) | Python heap after import | Estimated import on the Pi |
| --- | --- | --- | --- | --- |
| dashboard (`jittersim.api.server`) | 271 ms | 55 MB | 17.3 MB | 1.4 to 2.2 s |
| helper (`jittersim.networking.helper`) | 158 ms | 46 MB | 13.8 MB | 0.8 to 1.3 s |
| service watch (`jittersim.networking.dns_watch`) | 159 ms | 46 MB | 13.9 MB | 0.8 to 1.3 s |
| ad blocking (`jittersim.networking.adblocking`) | 107 ms | 35 MB before, 27 MB after | 11.6 MB before, 6.4 MB after | 0.5 to 0.9 s |
| browser proxy (`jittersim.networking.browser_proxy`) | 76 ms | 27 MB | 11.7 MB before, 6.5 MB after | 0.4 to 0.6 s |
| mdns (`jittersim.mdnsname`) | 23 ms | 16 MB | 2.9 MB | 0.1 to 0.2 s |

Idle CPU, measured over 30 s with no client: the browser proxy used 2 ms, the dashboard with
helper and scheduler used 15 ms when nothing was applied and 59 ms after one Apply (the
scheduler ticks every second and reads the gateway state from the helper each time). With the
Pi 5 to 8 times slower that is under 0.1% and about 1 to 1.6% of one core.

With 4 tabs open the target used 0.9% of one Mac core (17.5 s of CPU in 32 minutes), about 5
to 7% of one Pi core, and the helper ran 4,639 network commands in 32 minutes: 2.4 a second.
On the Pi each is a process start. The commands by tool: `tc` 1,649, `ip` 1,066, `nft` 918,
`iw` 816, `nmcli` 211. Helper calls by action in the same run: `gateway-status` 1,766 (the
scheduler, once a tick), `ping` 1,555 (every health poll), `clients` 816, `traffic-counters`
427, `uplink-info` 211, `update-status` 207, `queue-stats` 176.

### Top offenders and fixes

| # | Offender | Cost | Fix | State |
| --- | --- | --- | --- | --- |
| 1 | Scheduler opens a repository every tick | 1 ms CPU a second on a Mac, schema work 3,600 times an hour | keep one connection, drop it when a tick leaves a write open, close it on stop | fixed |
| 2 | Quota samplers rewrite unchanged counters | up to 720 journaled SD writes an hour per quota | skip the UPDATE when the counter did not move | fixed |
| 3 | `requests` loaded in the proxy and the ad-blocking service | about 5 MB of heap each | import it in the fetch child only | fixed |
| 4 | Each tab's traffic poll runs `nft` and `tc` | 2 commands every 2 s per tab on the Traffic page | share one reading between tabs for 1 s in `ControlServer` (not a pinned file) | proposed |
| 5 | Each tab's health poll pings the helper | one helper connection and thread every 5 s per tab | share the ping result for 2 s in `ControlServer` | proposed |
| 6 | Router page polls `/api/uplink` every 5 s and the helper runs `nmcli` | `nmcli` is the costliest command on a Pi (D-Bus round trips) | cache the uplink reading for 15 s, drop the cache on any write | proposed |
| 7 | The scheduler asks the helper for the gateway state every tick | one helper connection and thread a second while a plan runs | none: the tick needs it to see a Panic latch at once | left as it is |
| 8 | Service watch imports the whole helper module for `HelperClient` | 14 MB heap, of which the client needs a small part | move `HelperClient` to its own module (needs a change to `networking/helper.py`, which is pinned) | proposed, pinned |
| 9 | Time quotas write every 5 s while quiet | 720 commits an hour per enabled time quota | keep `last_sample_at` in memory and write it only with a change of the usage | proposed |
| 10 | Rollback journal with full sync | every changed commit is 3 or more syncs | WAL with `synchronous = NORMAL`. It needs care: other users read the database (the CLI and the update scripts) and WAL needs write access to the folder for its `-shm` file | proposed, owner decision |
| 11 | Every request is a new TLS connection (the server speaks HTTP/1.0 and closes after each answer, which keeps a worker per request) | one handshake per poll, 7 a second with 4 tabs; ECDSA keys and session resumption keep it small | measure the handshake share on the Pi with the budget and soak scripts before changing anything | measure first |

Proposal 8 is the only one that touches a Panic-pinned file. It is not needed for stability. If
the owner wants it, the smallest patch is a new module `jittersim/networking/helper_client.py`
that holds `HelperClient` and `HelperClientError`, with `networking/helper.py` importing them
from there (two lines), so the approved helper behaves the same. That change needs a new
`PANIC_APPROVED_SOURCES` entry after an independent review, and it is not part of this pull
request.

## What remains

- A run of several hours on the Pi itself (`python -m tests.soak.harness --minutes 240` from a
  checkout, with `--plain-http` if the throwaway certificate cannot be made) would replace the
  estimates above with real Pi numbers. The budget script prints the Python heap on any
  machine.
- The fixture has no traffic counters, no service watch and no ad-blocking daemon, so the
  quota samplers, the DNS watcher and the per-second ad-blocking tick were checked by reading
  the code and by unit tests, not by the soak.
- Proposals 4 to 6 and 9 change dashboard behaviour that browser journeys check, so they belong
  in their own pull request with the browser gate.
- Retention of old releases under `/var/lib/jittersim-updates` was not part of this audit.
