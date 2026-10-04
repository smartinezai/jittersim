# JitterSim verification on 1 October 2026

## Result

Release `959c2bc06a3e76862d3d75bdf856bf0f7ef36f31` is installed on the Pi.
Its four changed modules match the tested source. The full live Wi-Fi run
passed all 29 feature checks plus its completion check. All 188 dashboard
requests succeeded. The Pi run took 10 minutes and 16.5 seconds including
setup and restoration.

All 503 Python tests passed. Both browser journeys and all eight checks in a
fresh Linux image passed. The final concurrent traffic check passed on both
Linux and the Pi. Three defects were repaired and installed.

The saved configuration, Pi Wi-Fi settings and saved dashboard access were
restored. The saved configuration was then reapplied. All five Pi services
are active and no verification profiles remain. The Mac is back on its home
network and the VPN is connected.

The physical WPA3 connection check remains pending because the Mac locked.
Computer control requires the owner to unlock it. The completed physical
traffic run used the existing WPA2 configuration.

## Repairs

- Switching to packet duplication could fail while another old packet queue
  remained attached. The renderer now clears every old device queue on an
  interface before adding its replacements. A command-order test and a real
  Linux transition check protect the repair.
- Saved dashboard access could return 403 while Linux refreshed a connected
  device's address. Existing permissions now remain usable with one valid
  address mapping and a current Wi-Fi connection. Adding access still needs
  a freshly confirmed mapping. Tests cover address reuse, missing stations,
  invalid mappings and immediate permission removal.
- Several unfinished secure connections could delay a fresh request after a
  Wi-Fi change. The server now advances those connections together with fixed
  deadlines and a sixteen-connection limit. Configuration requests remain
  sequential. Real HTTPS tests cover idle connections, capacity, input
  deadlines and shutdown.

These findings came from the current tests. The original two reported
failures did not include enough detail to establish their exact causes.

## Automated checks

| Check | Result |
| --- | --- |
| Complete Python suite | 503 passed in 26.13 seconds |
| Focused lint | Passed |
| Served Wi-Fi recovery browser journey | Passed |
| Served dashboard access browser journey | Passed |
| Fresh Linux image without cached build steps | Built successfully |
| Complete suite in the fresh Linux image | Passed |
| Installer permissions and imports | Passed |
| Real Linux address identity and saved access | Passed |
| Real packet queue transitions and recovery | Passed |
| Protected clients, address reuse and global rules | Passed |
| Management name isolation and upgrade | Passed |
| Dashboard and private-network isolation | Passed |
| Concurrent real TCP checks in Linux | 10 cases passed |
| Concurrent real TCP checks on the Pi | 14 cases passed |

The Pi concurrent checks included independent upload and download limits,
combined packet effects, duplication with another rate limit, duplication on
both clients, loss isolation, protection, global denial and reset recovery.
Each client used a separate network namespace. These rates measure the Pi's
traffic rules and do not measure physical Wi-Fi capacity.

## Physical Wi-Fi checks

| Feature | Result |
| --- | --- |
| Normal internet, proxy traffic and dashboard reads | Passed |
| Delay in both directions | Passed |
| Bandwidth cap and live graph limits | Passed |
| Delay, jitter, loss, reordering, duplication and corruption together | Passed |
| Full packet loss while dashboard controls remain reachable | Passed |
| Device protection | Passed |
| Global device block overrides protection | Passed |
| Device allowed list rejects an excluded client | Passed |
| Device allowed list permits the selected client | Passed |
| Global website block | Passed |
| Global allowed website list | Passed |
| Group profile overrides the device profile | Passed |
| Real DNS observations | Passed |
| Selected website delay through the browser proxy | Passed |
| Device byte quota | Passed |
| Quota reset and recovery | Passed |
| Shared group byte quota | Passed |
| Weighted service byte quota with unrelated traffic unaffected | Passed |
| Service time quota | Passed |
| Active schedule | Passed |
| One-minute timer expiry | Passed |
| Progression driven by observed DNS queries | Passed |
| Progression driven by transferred bytes | Passed |
| Timed progression and recovery | Passed |
| Randomized delay changes | Passed |
| Random outages with dashboard access preserved | Passed |
| Randomized website choices and recorded history | Passed |
| Reset recovery | Passed |
| Final normal traffic | Passed |

### Measurements

Normal example.com requests took about 0.093 seconds. A 300 ms delay in each
direction increased them to 2.480 and 2.536 seconds.

The uncapped 0.5 MiB download measured 16,819 kbit/s. With a 1,000 kbit/s cap,
the same transfer measured 785 and 854 kbit/s. A cap sets a maximum speed.
The check requires completed transfers below the cap with a small allowance
for short-transfer variation. It does not require a minimum speed.

The selected website took 2.519 seconds through the delayed proxy. An
unrelated website took 0.131 seconds through the proxy and a direct request
to the selected website took 0.080 seconds.

The DNS progression reached its query target. Timed tests waited through
actual quota and timer expiry. Randomized website history contained recorded
choices. The configuration backup download had a valid SQLite file header.

## Router switching

Router switching had already passed 474 requests through a full off/on test
on the preceding release `23ea96e`. All 238 requests during the confirmed
two-minute off period passed. One later request timed out during the deliberate
Pi Wi-Fi restart used for restoration. Router-switch code is unchanged by the
three repairs.

## Recovery and limits

The installer saved private recovery copies before replacing code. It
verified that credentials, saved access and Wi-Fi settings survived
installation. The final configuration matches the snapshot taken before the
live run. Runtime state is now applied and all five services are active.

The additional client's saved settings were restored. Its reconnection was
not independently confirmed. It was absent from the Pi's station list and
existing management-network neighbor records at the final check.

The tested Linux 6.8 kernel rejects duplication alongside a second impaired
queue in the same tree. The installed Pi passed both mixed and two-client
duplication checks using real traffic.

One Mac supplies the physical Wi-Fi test. Separate namespace clients supply
concurrent traffic. Household coverage, physical client capacity and client
connections in the other security modes are not established by this run.

Credentials and response bodies are excluded from the
[saved test results](test-results/full-verification-2026-10-01.json).
