# Share polled readings between dashboard tabs, 2026-10-23

Proposals 4, 5, 6 and 9 of the [soak and resource audit](soak-and-resource-audit-2026-10-18.md).
Each open dashboard tab polls the helper, and the helper starts a process for every network
command it runs. On a Pi 3B+ that is the largest remaining steady cost. This change makes tabs
share one reading for a short time and stops one SD card write that carries no information.

No Panic-pinned file changes (`helper.py`, `access.py`, `panic_guard.py` stay as they are).

## What changes

| # | Reading | Who polls it | Shared for | Where |
| --- | --- | --- | --- | --- |
| 4 | Live traffic counters (`nft`, `tc`) | Traffic page, every 2 s per tab | 1 s | `GET /api/traffic` |
| 5 | Helper ping | health poll, every 5 s per tab | 2 s with the token, 1 s without (unchanged) | `GET /api/health` |
| 6 | Uplink (`nmcli`, `ip route`) | Router page, every 5 s per tab | 15 s | `GET /api/uplink` |
| 9 | `last_sample_at` of a time quota | scheduler, every 5 s | not stored while quiet | `record_time_usage` |

## Decisions

- One tiny helper, `SharedReading` in `src/jittersim/api/shared_reading.py` (about 60 lines, no
  dependency). The first caller reads. A caller that arrives during the read waits and takes the
  same result or the same error. A caller within `max_age` seconds after a good read takes its
  result. Each caller names the age it accepts, so the two health windows use one reading.
  Python's `functools.lru_cache` has no age, and `cachetools` would be a new dependency for 60
  lines, so a small class is the smaller choice. The health check already had this mechanism
  (PR 159). It now uses the same class and its own lock code is gone.
- Defaults are constants in `server.py`: `TRAFFIC_REUSE_SECONDS = 1.0`,
  `DASHBOARD_HEALTH_REUSE_SECONDS = 2.0`, `PUBLIC_HEALTH_REUSE_SECONDS = 1.0` (unchanged),
  `UPLINK_REUSE_SECONDS = 15.0`. No setting for any of them.
- Errors are never kept. Callers that waited for the same failing read get its error. The next
  request reads again. This also stops tabs from queueing one after another behind a helper that
  hangs. The one exception is the health check: a ping that fails is the answer ("degraded"),
  so it is kept for its window exactly as PR 159 kept it. That is what stops a device on the test
  Wi-Fi from turning an outage into a stream of helper calls.
- Every write drops all three readings, before it starts and after it ends (`write_response_for`,
  the one entry of every dashboard write: Apply, Reset, Panic, Wi-Fi, router and access changes,
  profile and device saves). Dropping before keeps a read during the write honest. Dropping after
  stops a reading taken during the write from being kept. It is deliberately not a list of paths
  that change the uplink: a missed path would show a stale uplink as a real defect, and an extra
  drop only costs one reading.
- Panic and Reset never wait on these readings. `drop()` takes no lock: it replaces a marker, and
  every kept result and every read in progress that belongs to an older marker is ignored. A read
  that was in progress when Panic dropped the readings still answers its own callers but is not
  kept and is not joined by later callers.
- `GET /api/traffic` keeps everything that is not the helper's counters per request (the
  limits, the reset message, the queue limits). After Reset or Panic the route answers the reset
  message before it reaches the shared reading. `sampled_at` is now the time the helper was
  read, so a rate computed between two readings uses the right interval.

## Staleness, reading by reading

- Traffic, at most 1 s old. The page draws a rate from two readings and its own clock. With one
  tab nothing is shared (the poll is 2 s). With several tabs the later tab gets a reading up to
  1 s old and the rate can be off by up to half for one sample if the tabs sit at the edge of the
  window. A steady offset between two tabs gives a steady age and no error.
- Health, at most 2 s old with the token. The page shows the helper as answering or not. A
  restart of the helper shows up 2 s later at worst, well inside the 5 s poll. The update health
  check (`updates.healthy`) also asks with the token. The security audit (A2) said that every
  request with the token asks the helper so this check sees the live state. It still does where
  it counts: it first asks systemd whether each service is active, and it tries again every
  second for up to 30 s, so a kept "degraded" delays a good answer by at most 2 s. A restarted
  dashboard starts with nothing kept.
- Uplink, at most 15 s old. The values (connection name, gateway) change when the owner changes
  the Pi's network or plugs the cable, not with anything the dashboard does. A change through the
  dashboard drops the reading, so the page that follows reads again. A change made outside the
  dashboard (a cable, the router's DHCP, a command on the Pi) shows up after at most 15 s.
- A change made by the scheduler or by `jittersim` on the Pi (a scheduled plan change, the
  command line Panic) does not pass through `write_response_for`. Its effect on traffic counters
  shows after 1 s at most. It does not change the uplink.

## Time quotas (proposal 9)

`last_sample_at` was stored every 5 s for every enabled time quota, also while the device sent
nothing. Each such `UPDATE` is a journaled write with several syncs on the SD card.

Now:

- The scheduler remembers the time of its last sample of each quota in memory
  (`_time_quota_sampled_at`) and passes it to `record_time_usage` as `previous_sample_at`.
- A sample whose counter did not move stores nothing, unless the quota has never stored a
  sample (stored `last_sample_at` is 0). That first sample is stored as the baseline, as before.
- A sample whose counter moved stores the counter, the used seconds and `last_sample_at` together.
  The seconds charged are `min(60, now - later of (stored time, remembered time))`, the same
  as before for a run that stays up.
- A protected (excluded) device's quota used to store `last_sample_at` every 5 s. It is only
  remembered now, so nothing is charged for the time the device was excluded.
- `rebase_time_quota_sample` is removed (nothing else called it). Apply, start and reset still
  store `last_sample_at`, and the later of the stored and the remembered time wins.

Restart. The memory is empty after a restart, so the first sample that sees traffic counts from
the stored `last_sample_at`, which is the last sample that changed something. It is charged
`min(60, now - stored)`. Before this change it was `min(60, now - the last quiet sample before
the stop)`. The two differ only when the device was quiet before the restart and sends traffic in
the first sample after it: up to 60 s are charged instead of the time of the restart. An active
device is stored every sample, so for it nothing changes. Used seconds and the counter are never
lost: they are stored whenever they change. A longer stored gap never charges more than the 60
s cap that already limited every sample.

## Not done

- No flush of the remembered times when the service stops. It would make an orderly restart
  exact, but it adds a write to the stop path for a difference of at most one minute.
- `/api/diagnostics`, the scheduler and the other helper reads are not shared. The scheduler's
  one read a second is needed to see a Panic latch at once (audit proposal 7).

## Tests

- `tests/unit/test_shared_reading.py`: hit, expiry, each caller's age, drop, error not kept,
  waiters share a result and an error, a read in progress during a drop, drop never waits.
- `tests/unit/test_shared_polled_readings.py`: the three routes through a real `ControlServer`:
  hit, expiry, error not kept, concurrent requests, every kind of write drops each reading, Reset
  and Panic never show kept counters, a reading taken during a write is not kept, drop does not
  wait for a blocked read, both health windows.
- `tests/unit/test_time_quota_quiet_samples.py`: no write while quiet, the charge after quiet,
  the stored baseline, an excluded device, and the restart in both cases.
- Existing: the public health tests (PR 159) and the route characterisation test still pass. The
  full `browser` run is the gate (`browser --changed` maps `server.py` to every module), and the
  soak harness measures the effect (see the audit, "After proposals 4 to 6 and 9").
