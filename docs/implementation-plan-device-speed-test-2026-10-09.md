# Device speed test: plan (2026-10-09)

## Summary for review

The owner asked for "a button you can click to test the speed of a device" so that the
speed before and after a profile can be compared, maybe with the Cloudflare speed test.

Phase 1 (built in this change) adds a **Test speed** button to each connected device on the
Devices page. It opens a window that:

1. starts a bounded measurement on the Pi for that one device (45 s by default, 15 to 90 s,
   one at a time per device, at most two at once, Finish now or Cancel at any time),
2. shows a link and a QR code to a public speed test that the person opens **on the device**
   (Cloudflare by default, LibreSpeed as the one alternative),
3. while the device runs that test, the Pi reads the counters it already reads for the
   Impact card once per second and works out the real download and upload throughput that
   went through the shaped path (peak, average while busy, median and 90th percentile per
   second, bytes, drops), plus the round trip and loss from the Pi to the device,
4. saves the result with the time, the device, the profile that was in force and its values,
   the protection state, the measurement source and a free label,
5. lists each device's results, lets the person type in the numbers the speed test page
   showed on the device, and compares a baseline result (for example "No limits") with a
   later one: the change in each number and whether the measured speed matches the
   profile's cap,
6. exports the results as CSV or JSON. The export holds no token, password or address of
   the dashboard.

Nothing changes in the privileged helper or in any Panic-gated file. Every new route needs
the admin token and the same saved-device check as every other dashboard route.

## Why this design

The Pi cannot run a speed test "as" the phone, and clients on the test Wi-Fi are firewalled
from the dashboard port except for the sign-in page. So the test traffic must come from the
device itself and the Pi can only watch it pass. The Pi already has two readings per device:

| Reading | Where | Per device? | Used by |
| --- | --- | --- | --- |
| `queue-stats` (tc counters of each queue) | helper action, read-only | yes, when the device has its own queue | Impact card |
| `traffic-counters` (nftables counters) | helper action, read-only | yes, when the device is in the applied plan | Traffic chart |
| Wi-Fi station counters (`iw station`) | not exposed by the helper | yes, for every client | nothing yet |

The queue counters are the best fit: they count what each queue actually sent, which is the
speed after the profile's cap, delay and loss. Upload is counted on the uplink after the
upload queue and download on the test Wi-Fi after the download queue. The Impact card already
trusts these numbers and compares them with the cap.

**Limit that follows:** a device only has its own queue while a saved profile applies to it.

* A device with its own profile: measured on its own queues (source `device`).
* A device covered by **Same as everyone** (general profile) or by **Everyone else**: measured
  on the shared queue (source `everyone` or `everyone-else`). The result is marked as shared
  because other devices' traffic is in the same counters.
* A device with no profile or a reset network: the Pi has no counter for it (source `none`). The
  run can still be recorded and the person can type in what the device's speed test showed. A
  missing helper or unavailable connection status is different: starting is refused with 503
  because JitterSim cannot confirm the device is connected. For a Pi-measured baseline, save a
  profile with nothing set (the form allows it; the Pi then gives the device a plain fair queue
  with no limit) and apply it, test, then apply the real profile and test again.

Reading the Wi-Fi station counters would also cover unshaped devices. That needs either a new
helper action (Panic-gated: helper.py) or a new `iw` subprocess with interface configuration in
the unprivileged dashboard. Both are left as an owner decision (D3) rather than added here.

### Latency and loss without the helper

The dashboard process sends one ICMP echo per second to the device's own address with an
unprivileged ICMP datagram socket (`socket(AF_INET, SOCK_DGRAM, IPPROTO_ICMP)`, allowed for all
groups by systemd's default `net.ipv4.ping_group_range` on Raspberry Pi OS). No subprocess, no
raw socket, no capability, no helper change. Before it sends anything it checks with a
`connect()` (which sends no packet) that the Pi itself has an address in the device's /24, so a
development machine or test run never sends echoes to a real home network. If the socket is not
allowed or the device does not answer (Windows blocks echo by default), the result says
"no answer" or "not available" instead of a number. For a device with its own profile the round
trip is Pi to device over the test Wi-Fi and passes the device's download queue, so it includes
the profile's download delay and the queueing caused by the running speed test ("loaded
latency"). For **Same as everyone** and **Everyone else** the Pi's own echo does not pass the
shared download or upload queue (the mark is only set in the forward chain), so the result says
"unshaped Wi-Fi round trip" and the note says it does not include the profile's delay. The echo
goes to the address the device has now, taken from the connected-device snapshot, never to the
address of a saved row.

### Which public speed test

Owner guideline: prefer a mature free tool over custom code. Options compared:

| Option | Runs where | Licence | Fit |
| --- | --- | --- | --- |
| **Cloudflare speed test** (speed.cloudflare.com, engine `@cloudflare/speedtest`) | page on the device | engine MIT | Default. No install or account, download, upload, latency, jitter and loaded latency, uses Cloudflare's edge so the test leaves the Pi through the shaped uplink. Cloudflare collects each result for aggregate statistics (README). |
| **LibreSpeed** (librespeed.org public page; self-host server optional) | page on the device | LGPL-3.0 | Offered as the alternative. Open source, no account. A self-hosted LibreSpeed on the Pi would be wrong: device to Pi traffic never passes the upload queue, so upload would look unshaped. |
| **M-Lab NDT7** (speed.measurementlab.net) | page on the device | Apache-2.0 | Not offered. M-Lab publishes every result with the tester's public IP address indefinitely (M-Lab privacy policy), which is a poor default for a household tool. |
| **iperf3** | needs an app on the device and a server | BSD | Not offered. Phones need a third-party app; a server on the Pi measures only the Wi-Fi hop and misses the upload queue; a public iperf server is not dependable. |
| fast.com | page on the device | closed | Not offered: no published API or engine. |

The QR code: the two allowed addresses are fixed in code, so their QR codes were generated
once with macOS's built-in CoreImage `CIQRCodeGenerator` (error correction M), checked by
decoding them with CoreImage's `CIDetector`, and stored as module rows in the script. No QR
library is added to the page, no new dependency is installed, and the page draws the code as
inline SVG, which the strict CSP allows. One unit test checks the shape of each code (size and
finder patterns). Another pins the two addresses and a SHA-256 hash of the stored rows, so a
change to an address or to a stored code has to be made on purpose.

### Cloudflare terms and limits found

* The engine `@cloudflare/speedtest` is MIT licensed. Its README says results are collected
  by Cloudflare on completion for aggregated insights, and its public TURN server for packet
  loss is deprecated.
* Neither the README nor the public page publishes terms or rate limits for calling the
  `speed.cloudflare.com/__down` and `__up` endpoints from another application. Third-party
  reports describe throttling under heavy repeated use.
* Phase 1 only links to Cloudflare's own page, which the person opens like any visitor, so no
  endpoint is called by JitterSim. Phase 2 would call the endpoints from the device's browser and
  should stay within one test per run with the default sizes (see below).

## Behaviour

* **Test speed** appears on each connected device row. It is disabled for a protected device
  (protected devices are never shaped and their traffic is not monitored) and while the Wi-Fi
  connection status is unknown. The server enforces the same: a start is refused (409) unless the
  device is associated right now, and refused with 503 when the status is unknown, the helper is
  missing or the counters cannot be read after three tries 0.25 s apart. The helper reads happen
  before the manager lock is taken, and the limits are checked again inside it.
* The window has three parts: start (speed test site, length, label), running (live seconds
  left, live download and upload, the link and QR code, Finish now, Cancel), and results
  (history for this device, typed-in device numbers, compare, export).
* Starting first checks the connected-device snapshot and reads the queue counters. These helper
  reads run outside the manager lock and may take a few seconds. After they finish, the run is
  returned while the Pi samples once per second in a background thread. When the time is up, or
  on Finish now, the result is saved. Cancel discards it.
* A run stops early and is saved with status `changed` when the device's queues change during the
  window (a new apply, a reset, counters that go backwards). A sampler error ends it as `fault`,
  and so does a run in which more than half of the reads failed (`read_failures` is kept).
* A device that becomes protected during a run: the run ends and nothing measured is saved. When
  a result is saved, protection is checked with the save. Cancel saves nothing.
* The saved profile is the one the scheduler's applied snapshot reports (saved assignment, group
  profile, schedule, timer, scenario, progression or data limit). If the snapshot cannot confirm
  the source, the row says `Unknown (source not confirmed)` instead of showing the saved
  assignment as applied.
* A service rule or service list sends some traffic through queues the Pi does not read here. The
  run says so, and the cap verdict is replaced by `service-rules` (not judged).
* A dashboard restart drops a running window (it lives in memory); nothing half-done is saved.

### Verdicts per direction

From the per-second rates while the device was busy (seconds at or above 10% of the peak):

* `too-little-traffic` when fewer than 3 busy seconds or under 64 KiB moved: the person probably
  did not run the speed test during the window.
* `no-cap` when the profile sets no speed for that direction.
* `at-cap` when the 90th percentile is at least 90% of the cap and the median is at most 110%.
* `above-cap` when the median is above 110% of the cap (shaping did not hold).
* `below-cap` when the 90th percentile stays under 90% of the cap (the internet or the device
  was slower than the cap).
* For a shared source the verdict is prefixed by the note that other devices share the counters.

## Data model

New table, created with `CREATE TABLE IF NOT EXISTS` and `CREATE INDEX IF NOT EXISTS` when the
repository opens (automatic and idempotent; older databases gain it, newer code can open it
again):

```
speed_test_results(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at INTEGER, finished_at INTEGER,
  mac_address TEXT, device_id TEXT NULL, device_name TEXT,
  label TEXT DEFAULT '', status TEXT, provider TEXT, source TEXT,
  protected INTEGER,
  profile_json TEXT,   -- profile in force and its upload/download values, general/everyone-else ids
  summary_json TEXT,   -- measured numbers (download, upload, latency)
  reported_json TEXT DEFAULT '{}'  -- numbers typed in from the device's speed test page
)
```

Bounded: the newest 50 results per device and 500 in total are kept. Labels are at most 80
characters without control characters. The stored rows hold no token, password, Wi-Fi secret
or dashboard address. They are part of the configuration backup like the activity log and are
not part of the settings export/import.

## API (all need the admin token; reads also pass the saved-device check)

| Method and path | Body or query | Answer |
| --- | --- | --- |
| `GET /api/speed-tests` | `mac` optional | providers, limits, active runs, saved results (newest first, at most 50 for one device or 200 overall) |
| `GET /api/speed-tests/status` | `run` | one active run with its live per-second rates |
| `GET /api/speed-tests/compare` | `baseline`, `result` | both results and the change per number |
| `GET /api/speed-tests/export` | `format=csv|json`, `mac` optional | the file text |
| `POST /api/speed-tests/start` | `mac_address`, `provider`, `duration_seconds`, `label` | the new run (201), 409 when one already runs for this device or two run in total, 409 for a protected device or a device that is not connected, 503 when the connection status or the counters cannot be read (retry) |
| `POST /api/speed-tests/finish` | `run_id` | the ended run: `state` is `ended` and `result` holds the saved row (404 when the run is unknown, 409 when it already ended) |
| `POST /api/speed-tests/cancel` | `run_id` | the ended run with `result` null and `ended_reason` `Cancelled. Nothing was saved.` |
| `POST /api/speed-tests/update` | `result_id`, `label`, `reported` | the updated result |
| `POST /api/speed-tests/delete` | `result_id` | `{deleted: true}` |

Every body is checked field by field; unknown fields are refused.

## UI

Self-contained `speed-test.js` and `speed-test.css`, added to the page by the assembler like
`workflows.js`. The button joins each row through the existing `connectedRowHooks` list, so
`renderConnectedDevices` is not edited. The window is a `<dialog>` made by the script. Live
numbers are in a polite status region announced once per change of state, not per second. Focus
moves to the window title whenever the sections change, and returns to a live **Test speed**
button (or the Devices link, when the list was redrawn) when the window closes. Delete asks for
confirmation first. A Finish or Cancel that finds the run already ended (409), lost (404) or the
Pi busy (503) asks for the run's real state again, so the window never stays stuck in the
running view.

## Security

* No new listener and no new unauthenticated path: everything sits behind the admin token and
  the saved-device check, refused before routing like other routes.
* No change to the helper, the access rules, Panic or the updater. The only new network action
  is one ICMP echo per second to a device on the Pi's own test subnet while a run is active.
* The provider is chosen from a fixed allow-list on the server; the page never builds a link
  from user input. The CSP is unchanged (`connect-src 'self'`, SVG drawn in the DOM).
* Bounded memory: at most two runs, at most 90 samples each, at most 120 echo times; the
  sampler thread stops with the run.

## Tests

* Unit: run summary maths (busy seconds, percentiles, verdicts, counter resets, shared source),
  storage migration on an old database and bounds, payload validation, protected refusal,
  one-per-device and global limits, finish, cancel, update, delete, compare, CSV/JSON export
  without secrets, the ICMP prober's subnet guard and parser, QR rows match the provider list.
* HTTP: token required for every new path, unknown fields refused.
* Browser journey `tests/browser/device_speed_test.py` at desktop and phone width: open the
  window from a device row, see the link and QR code, run a "No limits" baseline and a capped
  run, type in device numbers, compare, export, cancel, protected device disabled, no page
  errors, no horizontal scroll. Registered in `.github/workflows/wifi-recovery.yml`.

## Phase 2 (design only, needs an owner decision)

A device-side page served by the Pi that runs `@cloudflare/speedtest` in the device's browser
and posts the numbers back, so nobody types them in.

* Flow: the dashboard shows a short-lived code (for example 6 digits, valid 10 minutes, one use,
  bound to the device's MAC and the run). The device opens `https://<pi>/speed` on the test
  Wi-Fi, the page runs the library and posts `{code, results}` to the Pi.
* Security cost: a new route reachable from the test Wi-Fi without the admin token (the code is
  the only credential); a CSP for that page with `connect-src https://speed.cloudflare.com`
  (and the TURN host if packet loss is wanted); the library (about 40 KB, MIT) shipped with the
  release and pinned by hash; rate limits per device and per code (one result per code, five
  failed codes per minute per address); the posted numbers are untrusted and only stored.
  The control firewall already lets the test Wi-Fi reach port 8080 for sign-in, so no firewall
  change is needed, but the new route widens what an unsigned-in client can reach.
* Cloudflare: the endpoints have no published terms for third-party use; results are collected
  by Cloudflare. Keep the default measurement sizes and one test per code.
* Worth it when the owner runs many before/after comparisons and typing numbers is the
  bottleneck, or when the tested devices cannot open a public page and read numbers (for example
  TVs). Not worth it for occasional checks, where Phase 1 plus typed numbers is enough.

## Open owner decisions

* **D1** Default speed test site: Cloudflare (chosen) or LibreSpeed.
* **D2** Phase 2 device page: build it (accept the new token-less route behind a one-time code)
  or not.
* **D3** Pi-side numbers for devices with no profile: keep the "No limits" profile workaround,
  or approve a helper change (Panic-gated, independent review and a new bundle) that exposes the
  Wi-Fi station byte counters read-only.
* **D4** Retention: 50 per device and 500 in total, or other numbers.
