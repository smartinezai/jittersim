# Dashboard-served speed test: plan (2026-10-23)

## Summary for review

The owner asked that the device speed test should not need an outside server: the browsing
device downloads (and if possible uploads) bytes from the Pi and the dashboard shows the
throughput through the shaped path.

This change adds **This dashboard** as the first choice in the existing **Test speed** window.
When the window is opened on the device that is being tested, the Pi sends that browser
generated test data. The browser measures the speed, the Pi's own queue counters measure it
too, and the result is saved with the existing results, compare view and export.

What the code shows (details in "Facts" below):

* Traffic between a device and the dashboard port is **not shaped in either direction** today,
  on purpose: the renderer exempts every dashboard reply so the dashboard stays usable under a
  100% loss profile. A test on that path would only show the Wi-Fi speed.
* **Download** can be shaped: a reply that the dashboard marks with one DSCP value skips that
  exemption and goes through the device's own download queue (the same queue the Pi's ping
  already passes). Every other dashboard reply keeps the exemption.
* **Upload** to the Pi cannot be shaped without a new ingress shaping stage: upload profiles
  apply on the uplink (the Pi's Ethernet side), and traffic addressed to the Pi never goes
  there. An upload test would show the unshaped Wi-Fi speed and blame or credit the profile
  wrongly. **Upload is left out** of the dashboard test. The window says so, and the result
  marks upload as "Not tested".
* Shared queues (**Same as everyone**, **Everyone else**) are marked in the forward chain only,
  so the Pi's own traffic never passes them. The dashboard test is only offered for a device
  with a profile of its own (source `device`). Others get a clear message.

The outside-site mode (Cloudflare or LibreSpeed link, the Pi watches the queues) stays as the
second choice, because it is the only way to measure shaped upload and shared queues. Deleting
it is decision D1 below.

## Facts from the code

| Question | Evidence | Answer |
| --- | --- | --- |
| Is device to dashboard traffic shaped on download? | `networking/renderer.py`: `output oifname <test> tcp sport 8080 meta mark set DASHBOARD_REPLY_MARK`, then the download hook `meta mark DASHBOARD_REPLY_MARK meta mark set 0 return` ("keep local dashboard replies usable"). The pinned `networking/access.py` sets the same mark at output priority -250 and accepts it in its egress chain. | No, by design. |
| Can a local reply be shaped at all? | The device rule in the download hook is `ip daddr <device> meta mark 0 meta mark set <mark>`. It does not look at where the packet came from, so a Pi-made packet with mark 0 lands in the device's queue. `speed_test.py` already relies on this for the ping ("passes the device's download queue"). | Yes, for a device with its own queue. |
| Upload to the Pi? | Upload marks are set in the `forward` chain and the upload queues sit on the uplink. The input hook returns early for `tcp dport 8080`. A packet to the Pi takes `input`, never `forward` or the uplink. | Never shaped. |
| Shared queues? | `GENERAL_DOWNLOAD_MARK` and `EVERYONE_DOWNLOAD_MARK` are set in `forward` only. A Pi-made packet has mark 0 and only the device rules turn mark 0 into a queue. | Not shaped for shared queues. |
| Who can open the dashboard from the test Wi-Fi? | `jittersim-control-firewall.nft` accepts port 8080 from the test Wi-Fi. `ControlRequestHandler` then needs the admin token and `_check_device_access` (a device granted dashboard access). | Only a device with dashboard access and the token. Protected devices have access but are never shaped and are refused as before. |

### The renderer change

One rule after the existing reply-mark rule, in the experiment table (`renderer.py`, not pinned):

```
add rule inet jittersim output oifname "<test>" tcp sport 8080 ip dscp 0x02 meta mark set 0
```

The existing rule still marks every dashboard reply first (IPv4 and IPv6, unchanged). Only an
IPv4 reply whose DSCP is 2 has the mark cleared again, so the download hook puts it in the
device's queue. Only the dashboard process sends from port 8080 on the Pi, and it sets DSCP 2
only on the socket of one speed test download, which is always closed afterwards
(`Connection: close`), so no later dashboard answer can reuse it.

DSCP 2 (TOS byte 0x08) was chosen because mac80211 maps it to best effort like unmarked traffic
(it takes the top three DSCP bits, which are 0). Setting the TOS also sets the socket priority
to 2, which the Pi's own Wi-Fi driver (brcmfmac, not mac80211) may take as the background
class, so the dashboard sets the priority back to 0 right after (review of PR 189, F1). Both
parts should then use the same Wi-Fi class. This is not yet checked on a Pi. The pinned access table is not changed. While a MAC
allow or deny list is on, its egress chain accepts the reply mark before it drops a denied
device, so a speed test download to a denied device is dropped. Such a device cannot pass the
dashboard checks to start a test anyway.

## Behaviour

1. On the device to test, the person opens the dashboard (it needs dashboard access and the
   admin token), goes to **Devices**, and chooses **Test speed** on its own row. The page
   compares the browser's address (sent by the server with the speed test list) with the row's
   address and preselects **This dashboard** when they match. Otherwise the first speed test
   site stays selected.
2. **Start measuring** starts the usual Pi window (20 s) and then the page downloads:
   * **Reference** (3 s): unshaped dashboard data. This is what the Pi and its Wi-Fi can send
     to this device right now.
   * **Through the profile** (8 s): data marked with DSCP 2, so it goes through the device's
     download queue.
   The browser reads the stream with `fetch` and a stream reader and times it with
   `performance.now()`. The first second of each part is a warm-up (TCP slow start, the
   profile's queue filling) and is not counted. Speed = bytes after the warm-up × 8 / time from
   the first counted chunk to the last chunk. The parts are fixed in time, not in bytes, so a
   slow profile never makes the test run long.
3. The page then calls **Finish** with the two browser numbers. The Pi saves the result as
   before, with the status `complete`. Download keeps the Pi's own counters (average while busy, percentiles, the cap
   verdict). Upload says "Not tested: uploads to the Pi itself are not shaped". The browser
   numbers are kept as `summary.browser` and appear in compare and export.
4. **Limited by the Pi.** When the profile's download cap is at least 90% of the reference
   speed, the cap cannot show in this test: the verdict becomes `pi-limit` ("The cap is at or
   above what the Pi sends over Wi-Fi, so this test cannot show it") instead of a below-cap or
   at-cap verdict that would blame or credit the profile.

### Refusals (all with a sentence for the person)

| Case | Status |
| --- | --- |
| The browser is not the device being tested (address differs) | 403 |
| Device without its own queue (shared queue, no profile, reset network) | 409 |
| Panic or an outage keeps profiles stopped (`_stop_state` is `latched`), or the state cannot be read | 409 / 503 |
| Another dashboard test runs (any device) | 409 |
| The same device ended a dashboard test less than 15 s ago | 429 |
| A part was already sent, or a part runs now, for this run | 409 |
| The run is not running, unknown, or is an outside-site run | 404 / 409 |

## Limits and defaults

| Limit | Value | Why |
| --- | --- | --- |
| Dashboard tests at once (all devices) | 1 | Two streams would share the Pi's CPU and Wi-Fi and each would look slower. Outside-site runs keep their limit of 2. |
| Window length | 20 s | Reference 3 s + main 8 s + start and finish. |
| Server send time per part | part length + 2 s (5 s and 10 s) | The browser ends the part. The server stops in any case. |
| Bytes per part | 64 MiB | About 100 Mbit/s for the reference part's whole time and 50 Mbit/s for the main part's, above what a Pi 3B+ sends over Wi-Fi. A part that reaches it ends early and the browser measures what arrived. |
| Parts per run | each part once, one at a time | Bounds time and bytes per run. |
| Gap between dashboard tests of one device | 15 s | A script with the token cannot keep the Pi busy all the time. |
| Send buffer | one 16 KiB block of random bytes made once at import, sent again and again | No file, no memory growth. |
| Write timeout | the part's remaining time + 1 s | A client that stops reading frees the worker when the part's time is up. |

Worker threads: one test holds one of the 16 ordinary workers for at most 15 s and never one
of the 2 Panic workers (only an authorized Panic request is served from those). It takes no
helper call while streaming and does not hold the manager lock while sending. It does not
touch the browser proxy or its tunnel slots (port 3128 is a different service). The stream
stops at once when a Reset or Panic request arrives (`_resets_pending`) or the run ends (a
Panic makes the sampler end the run as `changed`).

Accuracy and the Pi's ceiling: the Pi 3B+ has one Wi-Fi stream (802.11ac on 5 GHz) and no AES
instructions, and the dashboard is Python over TLS. Published measurements put a Pi 3B+ access
point at a few tens of Mbit/s. This could not be measured here (no Pi access in this run), so the
test measures the ceiling every time (the reference part) instead of trusting a lab number, and
the `pi-limit` verdict uses it. Profiles that cap below the ceiling (the usual case for a
"slow network" test) are measured correctly.

## Security

* No unauthenticated path. The new `GET /api/speed-tests/download` sits behind the admin token
  and the dashboard-access check like every `/api/` read (refused before routing). It answers
  only the browser whose address is the device's current address from the connected-device
  snapshot.
* No request body: the upload part was left out, so there is no large-body route at all.
* Headers: the same `no-store`, `nosniff`, `X-Frame-Options`, `Referrer-Policy`, COOP and CORP
  as every answer (one shared helper now sends them), `application/octet-stream`,
  `Connection: close`, no `Content-Length` (the body ends when the connection closes).
* Panic and Reset paths are not touched. The hash-pinned files are not changed.

## Risks

| Risk | Likelihood | Effect | Handling |
| --- | --- | --- | --- |
| DSCP mark sticks to a later dashboard answer | Low | That answer is shaped by the device's profile | The connection always closes after the test part; the TOS is also reset before close. Unit test. |
| A heavy profile (100% loss) makes the part fail | Expected | The browser gets no data for the main part | The result says "too slow to measure"; the dashboard itself is unaffected (other connections keep the exemption). |
| The Pi's CPU or Wi-Fi limits the speed | Likely at high caps | A cap looks "not reached" | The reference part and the `pi-limit` verdict. |
| Streaming slows Panic | Low | Panic answer later | One stream at most, no lock or helper while sending, stream ends on Reset or Panic. |
| A script with the token loops tests | Low | Pi busy | 1 at a time, 15 s gap, 64 MiB and at most 10 s per part, two parts per run. |
| Kernel or driver ignores the DSCP rule | Low | Main part is unshaped | The main part would bypass the device queue, so the Pi's counters would read "too little traffic" while the browser shows a high speed. The kernel datapath test checks the rule. |
| Bytes still in the send buffer when a part ends by the byte cap | Low | They leave unshaped after the TOS is reset, so the browser's main-part number reads a little high | Only when a cap above about 54 Mbit/s reaches the 64 MiB limit. The Pi's queue numbers stay correct. In the usual case the browser ends the part and the buffer is dropped (review F5). |

## Tests

* Unit (`tests/unit/test_dashboard_speed_stream.py`): refusals in the table above, the byte
  cap per part, the time cap, one part at a time, each part once, rate limit, global cap, Panic latched
  and a Reset arriving mid-stream, an aborted connection frees the slot, the DSCP is set only
  for the main part and the connection closes, headers, the token, `pi-limit` verdict, upload
  "not tested", browser numbers validated.
* Renderer golden files: the new rule.
* Kernel datapath test: a local TCP reply from port 8080 with DSCP 2 is counted in the device's
  download queue and an unmarked one is not.
* Browser module `speed_test_window.py`: the dashboard flow on the fixture (browser address
  equals the device address, with Chromium's network throttled to about 8 Mbit/s so loopback
  does not finish a part inside the warm-up), the result shows the browser numbers and "Not
  tested" upload.

## Open owner decisions

* **D1** Delete the outside-site mode? It is the only way to measure shaped upload and shared
  queues, so it is kept as the second choice. Deleting it removes about 150 lines (providers, QR
  codes, link view) and those two measurements.
* **D2** Shaped upload from the dashboard would need a new ingress shaping stage on the Pi for
  traffic to the dashboard port (helper and renderer work, Panic-gated). Not built.
* **D3** Gap of 15 s and one test at a time: keep or change.
