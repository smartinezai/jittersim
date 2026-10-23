# Check connection: plan (2026-10-17)

## Summary for review

The owner asked for "a feature where you can click a button from the jittersim dashboard to send
some packages to a device and check the loss and so on to make sure it's actually working."

This change adds a **Check connection** button to each connected device, next to **Test speed**
(PR 121, which is merged). The Pi sends a bounded burst of small ping packets to
the device (100 probes at 10 per second by default, 20 to 300 allowed) and shows what came back:

* packet loss, with a 95 percent range,
* round trip: minimum, average, median, 95th percentile and jitter,
* answers that came twice (duplicates) and answers that arrived out of order (reordering),

and compares them with the profile the Pi has applied to that device (loss, delay, jitter,
duplication, reordering, corruption and speed cap). The answer is one plain verdict per number and
one overall verdict: **matches the profile**, **less than set**, **more than set**, or one of a few
"not judged" answers that say why (shared profile, service rules, a profile that changed during
the check, a set delay or cap that a short burst cannot measure, a device that ignores ping). Results are saved per device and can be exported as CSV or JSON
like the speed test results.

Nothing changes in the privileged helper or in any Panic-pinned file. The check reuses the PR 121
ICMP prober, manager, limits (one run per device, two in total, bounded time), association check,
service-rule check and result storage. It adds one pure module for the statistics, one route
module, one window script and one test set. No new dependency and no new network action: the only
packets are ICMP echo requests from the dashboard process to one device on the Pi's own test
subnet, the same kind the speed test already sends.

Two facts from the code shape the design and one of them corrects the brief (see "What the probe
passes"):

1. Only the **download** side of a device's own profile can be checked. The Pi's probe passes the
   device's download queue. The device's answer does **not** pass any upload shaping, because the
   upload marks are set only in the forward chain and upload loss, delay and rate run on the
   uplink toward the router, where an answer to the Pi never goes. The window says this.
2. A profile shared by everyone (**Same as everyone**, **Everyone else**) is not applied to the Pi's
   own packets at all, so there the check can only show the plain Wi-Fi round trip and says so
   instead of giving a verdict.

## Why this design

The Pi cannot see what a device does with a profile from the inside, but it can talk to the device
through the same shaped path the device's downloads use. Echo probes are the smallest tool for
that: every ping to a device passes the device's own download queue (the netdev egress hook marks
by destination address, see `networking/renderer.py`, `_render_nft_script`, the `ip daddr` rule),
so loss, delay, jitter, duplication and reordering of that queue show up in the replies. The
unprivileged ICMP datagram socket of PR 121 needs no helper change.

### What the probe passes (checked in the renderer, proved by the kernel test)

| Traffic | Queue it passes | Evidence |
| --- | --- | --- |
| Pi to device (echo request) | the device's own **download** queue on the test Wi-Fi interface | `add rule netdev jittersim_download download ip daddr <device> meta mark set <mark>` marks any packet to the address, forwarded or locally generated |
| Device to Pi (echo reply) | **no shaping** | the upload mark is set only by `iifname <test interface> ip saddr <device>` in the **forward** chain. A reply to the Pi is input traffic. Upload delay, jitter, loss and rate sit on the uplink egress. Only upload corruption runs on the ifb device, and only for connections that carry a class in the connection mark, which a Pi-originated flow never gets |
| Shared profiles | **not shaped** | the download marks of Same as everyone and Everyone else are set only in the forward chain (`GENERAL_DOWNLOAD_MARK`, `EVERYONE_DOWNLOAD_MARK`) |
| Protected device | not shaped | the `unaffected by profiles` rule returns before any mark |

So the brief's fact that "replies pass the upload path (ifb ingress...)" is not what the renderer
builds. The check measures the **download side** of the profile. The kernel test has a negative
control: 50 percent upload loss and no download loss gives no probe loss. If a later renderer
change makes replies pass an upload queue, that test fails and this plan must be revisited.

The upload side can be checked with **Test speed** (cap) and the Impact check (drops). The window
says so next to the numbers.

### Why ICMP only and no ARP probe (D1)

A layer 2 ARP probe would be answered by every device, even one that ignores ping. It is not worth
building:

* It needs a raw `AF_PACKET` socket (`CAP_NET_RAW`). The dashboard runs without capabilities, and
  the only way to get one is a new helper action, which is Panic-pinned (`networking/helper.py`).
* ARP is not an IPv4 packet, so the `ip daddr` marks never match it. It would only prove the
  device is reachable on layer 2, never that a profile takes effect.
* The check already requires the device to be associated with the Wi-Fi right now (the same
  association check as Test speed). That already proves layer 2 presence.

So "the device does not answer ping" is told apart from "loss" by the replies themselves: zero
replies in the whole burst is reported as **no answer to ping** (not as 100 percent loss) unless
the profile itself sets loss at the level that explains it. The window then says what to do: many
phones, TVs and PCs ignore ping (Windows blocks it by default), so use **Test speed** or the
Impact check instead. A helper read of the neighbour table could be added later if the owner wants
it (O3).

## Behaviour

* **Check connection** appears on each connected device row next to **Test speed**. It is disabled
  while the Wi-Fi connection status is unknown (same rule as Test speed). It is **enabled for a
  protected device**: such a device has no profile, so the check reports the plain baseline.
* The window has three parts: start (number of probes, label), running (probes sent and answered,
  Cancel), and results (the verdict for the last check, history for this device, export).
* Start refuses (same rules as Test speed, with the check's own wording): 409 when the device is
  not associated right now, 409 when a speed test or a check already runs for this device or two
  measurements run in total, 503 when the connection status or the helper cannot be read, 503 when
  the dashboard cannot send pings (the reason is named: not allowed, not on the device's network),
  409 when the device has no address yet, 503 while the dashboard stops.
* A run sends one probe every 100 ms for the chosen count, then waits up to a grace time for late
  replies (see "Time limits"). It never runs longer than 40 seconds in total.
* Cancel discards the run and saves nothing. A dashboard restart drops a running check (it lives
  in memory).
* The profile is the one the Pi has **applied**, read like Test speed reads it: the helper's queue
  counters give the impairments that sit on the device's download queue now, and the scheduler's
  applied snapshot names the profile, the source (saved, group, schedule, timer, scenario,
  progression, data limit) and the service rules.
* **Changes during the check.** The check promises to catch changes it observes, not every
  transient one. It compares, with no verdict as the result (status `changed`, the numbers are
  kept), for **every** run including a baseline or shared one (a device that starts without a
  profile and gets one during the burst is not a baseline): (a) once per second during the burst
  and once more at the end, from the scheduler's applied plan **in memory** (no helper call): the
  saved device whose queue the plan marks this address into, the impairment of that download
  queue, its source (kind, identity, profile: the label and step only word a message), whether shared profiles exist and the service-rule state, so a
  progression, timer, new apply or reset is seen; (b) after the burst, from the helper, for a
  device with its own profile: the queue identity (generation, interface, handle, kind,
  impairment); for every run: whether the device is still associated and **whether it still has
  the address that was probed** (a connected device whose address is missing or different is
  changed), and from the repository its protection. A change that goes away again between two
  readings is not seen (A to B to A within a second). A cancel or a dashboard stop during the
  final reads starts no further helper read. A network with no applied plan (reset) has its own
  "no plan" reading, so a plan applied during such a check is a change too.
* **Locks at the end.** The manager lock comes first. The applied plan is read (the scheduler's
  runtime lock) and the summary is built before the repository transaction that saves the row,
  never inside it: Apply and the scheduler hold the runtime lock while they write the repository,
  so reading the plan inside the transaction could make both wait for each other forever. The
  transaction only confirms the protection that judged the row and inserts it. If the protection
  changed in between, the row is judged again with the new value (at most three times, then
  nothing is saved).
* **Unverified end state.** If the final helper reads fail for a device with its own profile
  (queue read or association status), the settings might have changed in the meantime. The result
  is then saved with the numbers and the verdict `unverified` (no accusation). This is a different
  answer from a failed read at the start, which refuses to start.

### Verdicts

Each number has a status: `matches`, `less`, `more`, `extra` (delay or jitter above the range,
shown but never counted against the profile), `borderline`, `inconclusive` or `not-judged`.
The overall verdict combines them:

| Overall verdict | When |
| --- | --- |
| `matches` | at least one number is `matches` or `extra`, none is `less` or `more`, none is `borderline`. A profile that sets nothing on either side and a healthy link also give `matches` |
| `less` | one or more numbers are `less` and none is `more` ("less impairment than the profile sets") |
| `more` | one or more numbers are `more` and none is `less` |
| `differs` | some `less` and some `more` |
| `borderline` | no `less` or `more`, but at least one `borderline` (close, run again with more probes) |
| `inconclusive` | nothing could be judged because the probes cannot tell the set values from a healthy link. Says how many probes are needed |
| `upload-only` | the profile sets nothing on the download side but sets upload values. The probes pass no upload queue, so a healthy link says nothing about the profile: no verdict, the upload note comes first. A real `less` or `more` on the download side (a link that loses pings) still shows as that verdict, with the upload note first |
| `baseline` | no profile applies, the device is protected, or the network is reset. The numbers are the plain Wi-Fi path |
| `not-comparable` | the device is covered by Same as everyone or Everyone else: the Pi's probes are not shaped. Reads "unshaped Wi-Fi round trip, not comparable with the profile" |
| `service-rules` | a service rule, service list or active quota applies to the device (or could not be confirmed): verdict withheld like in PR 121 |
| `out-of-range` | the set delay plus jitter is over 4.5 seconds or the speed cap is under 16 kbit/s: a burst cannot measure it |
| `no-answer` | the device did not answer a single probe and the profile does not explain it |
| `changed` | the profile, the address, the association or the protection changed during the check |
| `fault` | the check stopped after an error: whatever matched so far is not a verdict |
| `unverified` | the final helper reads failed for a device with its own profile, so a change cannot be ruled out |

The order of the checks is: `changed`, `fault`, `unverified`, probes unavailable, `out-of-range`,
`no-answer`, `not-comparable`, `baseline`, service rules, `upload-only` (only when no number is `less` or `more`), then the combination
above. A change
or a fault comes first for every kind of run, so a baseline is never shown for a device that
left the Wi-Fi or got a profile during the burst.

### Per-number rules

All rules use the **download impairment of the applied queue** (`set` values), as the helper
reports it in its queue counters, including the burst length of the loss. The start check of the
speed test manager has already compared those numbers with the scheduler's applied plan, and the
check is refused when they differ. Loss uses the exact binomial model below.

| Number | What is measured | What the profile is expected to give | Judged when |
| --- | --- | --- | --- |
| Loss | probes without a reply by the end of the grace time | the exact independent model of netem (see below) with the corruption drop between `0.9C` and `C`, plus 2 points Wi-Fi allowance on the high side | always (a set loss of 0 is judged against the allowance). Not judged when duplication, corruption and bursty loss are all set |
| Delay | median round trip | `D - J` to `D + J + serialization + max(30 ms, 0.3 D)`. Below the range is `less`. **Above the range is not an accusation**: status `extra` ("the delay is in effect, the round trip is X ms above it, which the link or the device adds") because a phone can add 100 ms by itself | `D > 0`, no reordering set, at least 5 replies |
| Jitter | mean absolute difference of the round trips of probes that were sent one after another and both answered (the first answer of each) | simulated from `D`, `J`, the loss and the probe spacing with netem's ordered scheduling (see below), tolerance 0.6 to 1.4 times plus 5 ms. Above is `extra` like delay | `J > 0`, no reordering, duplication or corruption set, at least 20 replies **and** at least 10 adjacent pairs |
| Duplicates | probes answered more than once, among the probes answered | `Pd(1-L)^2 / (1-(1-Pd)L)` to `Pd` plus 2 points | always when `Pd = 0` (judged against the allowance). With `Pd > 0` only when loss is independent (no burst, length known) and corruption is 0. Otherwise "not judged: the share of doubled answers is uncertain" |
| Reordering | answers that arrived after the first answer to a probe sent later (RFC 4737 style, first answers only, so a late duplicate never counts) | simulated for the probe spacing and the loss (an answer that never comes is no evidence), tolerance 0.7 to 1.3 times plus 2 points | no duplication or corruption set, `D > 0` and the simulation expects at least 3 reordered answers. Otherwise "not judged: the delay is shorter than the probe spacing, so probes cannot overtake each other". With `R = 0` it is judged against the 2 point allowance |
| Corruption | not a separate number | a damaged ping is dropped by the device, so it is counted in the loss range | never separately |
| Speed cap | nothing | a burst of 98 byte frames at 10 per second is about 8 kbit/s, far below a normal cap | never. Says "use Test speed". Its serialization time is added to the expected delay |

**Out of reach for a burst.** The check refuses to judge, with the verdict `out-of-range` and the
reason, when the profile cannot be measured honestly: a delay plus jitter of more than 4.5 seconds
(valid answers would arrive after the longest wait of 10 seconds and look lost) or a speed cap
below 16 kbit/s (twice what the 10 probes per second need, so they would queue up and show as
loss). The probe is 56 bytes of data, 98 bytes on the wire.

**Delay and jitter with reordering.** Netem sends a share `R` of packets without the delay, so the
median round trip no longer shows the delay. When reordering is set, delay and jitter are not
judged (the reordering number is). **Jitter and reordering with duplication or corruption.** A
clone can come back before the original and a corrupted probe never comes back, so which answer
is the first one changes: both numbers are not judged then.

The **jitter and reordering simulations** reproduce netem's scheduling and use a fixed random seed,
so a result is repeatable: delay and jitter uniform around the set delay and never negative; a
packet that skips the delay (reordering) jumps to the head of the queue; the delayed packets keep
their order because the renderer always sets a rate when jitter is set (`ORDERING_RATE`), which
is also why independent samples of `D +- J` would be wrong. Because a simulation alone does not
prove the model, the kernel test compares the real kernel's jitter and reordering with it.

Why the loss and duplicate ranges look like this (netem, `net/sched/sch_netem.c`): the duplicate
decision comes first, then the loss decision. A duplicated packet that is also "lost" still leaves
one copy (the original passes the loss decision), and when the original is not lost its clone goes
through the queue again with its own loss and corruption decisions. With independent decisions and
a corruption drop `c` per delivered copy the probe is unanswered with probability
`(1-Pd)(L+(1-L)c) + Pd(L c + (1-L) c (L+(1-L)c))` and answered twice with probability
`Pd(1-L)^2` when `c = 0`. Without corruption that is `L(1-Pd)` unanswered. Corruption flips one
bit of the 98 byte frame. The device drops the frame unless the bit is in the source MAC address
(6 bytes), so `c` lies between `0.9C` and `C`: those two give the low and high end of the loss
range. (The first plan version put corruption on one surviving copy only, which wrongly accused a
correct profile with duplication and corruption: both copies must be damaged to lose the probe.)

### Statistics

* The interval is the **Wilson score interval** for a binomial share: closed form, standard
  library only, and valid at small counts and near 0 or 1 (a plain normal interval is not). Two
  levels are computed: 95 percent (`z = 1.96`) and 99 percent (`z = 2.576`).
* A number **matches** when its expected range overlaps the 95 percent interval. It is **less**
  or **more** only when the expected range lies completely outside the 99 percent interval.
  Between the two it is **borderline**. This is the brief's rule ("only claim matches when the
  configured value lies inside the interval") with a second, wider bar before a profile is called
  wrong. The wider bar makes a false accusation rare but **not** impossible: the Wilson interval
  has approximate coverage, the 2 point allowance sits on top of it, a link that really loses
  2 percent passes the loss rule with a probability of about 98.5 percent over 100 probes, and
  Wi-Fi loss comes in bursts that the binomial model does not know. The plan therefore promises
  "rarely wrong" and shows the numbers and ranges next to every verdict. The rule is unit tested
  with exact binomial tail sums for independent probes.
* **Bursty loss** (`loss_burst_packets` of 2 or more, Gilbert model of the renderer): successive
  probes are alike, so the interval widens. The sample count is divided by the variance inflation
  `(1+rho)/(1-rho)` with `rho = 1 - exit - enter` (the same formula as the kernel test
  `test_bursty_loss_keeps_the_configured_average`). With a burst length of 4 and 10 percent loss
  that is a factor of 6.2.
* **Telling the set value from a healthy link.** A set value that the 95 percent interval of the
  observation cannot tell from a healthy link (the interval also overlaps the 0 to 2 percent
  allowance) is `inconclusive`, not `matches`: for example 0 lost probes of 100 with a set loss
  of 3 percent. This is different from proving a mismatch (the 99 percent rule) and a set value of
  0 is exempt (nothing to tell apart). The **probes needed** the result names come from this same
  rule: the fewest probes (20 to 300) for which the interval of the expected share lies above the
  allowance, computed with the same burst inflation. With 100 probes a set loss of about 5 percent
  or more can be told from a healthy link (5 percent needs 84, 4.5 percent needs 121, 4 percent
  needs 189). Below about 4 percent more than 300 probes would be needed and the window says
  so. A bursty loss needs more (10 percent with bursts of 4 needs 73).
* **Zero replies:** if no probe was answered and the loss number does not `match`, the overall
  verdict is `no-answer`, not `more`.
* **Wi-Fi allowance:** a healthy link loses about 0 to 2 percent of pings by itself and adds a few
  milliseconds. The allowances above (2 points, `max(30 ms, 0.3 D)`) are assumptions, named as
  constants in `connection_check.py` and pinned by tests. A phone in power-save mode can add up to
  about 200 ms to the first pings: the window tells the person to wake the device first.

### Time limits

* Probe spacing is fixed at 100 ms (10 per second). Count 20 to 300, so the burst lasts 2 to 30
  seconds.
* Grace time for late replies: `clamp(2 * (D + J) / 1000 + 1, 1, 10)` seconds, 2 seconds when no
  profile is expected, 3 seconds when the delay is unknown. The wait ends early when every probe
  was answered. A reply later than the grace time counts as lost. A very long set delay (over 5
  seconds) is therefore reported with the note "replies later than 10 seconds count as lost".
* Hard upper bound of the probing: `count * 0.1 + grace` seconds, at most 40. The run stores its
  deadline and the loop stops at it whatever the prober does. The loop listens in steps of 100 ms,
  so a cancel or a dashboard stop is seen within 100 ms of the loop.
* The two final helper reads are **outside** that bound: one attempt each, so at most the helper's
  timeout (8 seconds) each. A stop during one of them ends the check within that timeout and the
  server reports `WorkersStillRunning` for it exactly like for a speed test sampler.
* **Late duplicates.** When the profile duplicates packets the wait for late answers is **not**
  ended early when every probe has its first answer (a second copy can still be queued behind a
  delay). It runs the full grace time. Without duplication the wait ends as soon as every probe
  is answered.

## Decisions

Each has a recommended default that this change ships. The owner can change any of them later
without a data migration.

* **D1** Probe type: ICMP echo only, reusing `IcmpProber`. No ARP probe (reasons above).
* **D2** Defaults: 100 probes, 10 per second, 20 to 300 allowed, rate fixed. Chosen so that the
  default run takes about 12 seconds and can judge a loss of 3 percent or more.
* **D3** What is checked: the download side of the profile only. The upload side is not part of
  the check and the window says so.
* **D4** Confidence: matches inside the 95 percent interval, less or more outside the 99 percent
  interval, borderline between.
* **D5** Shared profiles (Same as everyone, Everyone else): no verdict, `not-comparable`.
* **D6** Service rules, service lists and active time or byte quotas: **withhold the verdict**
  like PR 121 does, and also when the applied rules cannot be confirmed. Analysis for the owner:
  these rules match traffic from service addresses (`ip saddr <service>`) or in the forward chain,
  so a ping from the Pi is not diverted by them. The withhold is the conservative choice (the
  plan can change while the check runs, and the speed test treats the same cases the same way).
  Relaxing it later is a one-line change in `_service_rules_apply` use.
* **D7** Protected devices: the check works and reports `baseline`. (The speed test refuses them
  because it reads their queue counters; the check reads none for them.)
* **D8** Storage: a **second table** `connection_check_results` with the same columns, created by
  `CREATE TABLE IF NOT EXISTS` when the repository opens, served by a subclass of the existing
  `SpeedTestStore`, which gets a `table` attribute that every statement uses (one refactor of the
  store, the speed test behaves as before). Alternative considered: a `kind` column on
  `speed_test_results`. That needs an `ALTER TABLE` on existing databases, makes every query
  kind-aware and would show check rows in the speed test list if the code is downgraded. The
  separate table has none of these costs. Retention is the same: 50 per device and 500 in total
  per table. **Result numbers are per table** (both start at 1): a speed test route and a check
  route each address their own table, and run ids (random hex) are never shared.
* **D9** One run per device across both kinds, two runs in total across both kinds. A check and
  a speed test never run on the same device together (the probes would disturb the speed test's
  round trip and the speed test's traffic would disturb the check).
* **D10** Window: its own script `connection-check.js` and style `connection-check.css`, like
  `speed-test.js`. It reuses the speed test's small DOM helpers and table styles and adds no
  `window.confirm` (delete asks in the page's own dialog, `confirmWorkflow`, like the speed test).
* **D11** Corruption has no number of its own (counted as loss). Speed cap is never judged by a
  burst. Duplicates are not judged together with bursty loss or corruption. Delay and jitter are
  not judged together with reordering.
* **D13** A delay or jitter above the profile's range is shown as extra path delay and never
  counted against the profile. Only a round trip below the floor `D - J` says "less".
* **D12** Export: CSV and JSON with the check's own columns (`connection-checks` format name).
  Same safety as the speed test export: no token, password or dashboard address, spreadsheet
  formula cells escaped.

### Open owner decisions

* **O1** Whether to relax D6 (judge a device that has service rules), given that Pi pings are not
  diverted by them.
* **O2** Defaults of D2 (100 probes at 10 per second). More probes judge smaller shares but take
  longer.
* **O3** Whether to add a read-only helper action for the neighbour table, as a cheap second
  reachability hint. It would touch `networking/helper.py` (Panic-pinned), needing an independent
  review and a new bundle.
* **O4** Retention (50 per device, 500 in total).
* **O5** The confidence levels of D4 and the Wi-Fi allowances (2 points, 30 ms and 30 percent).
* **O6** Whether a baseline run (the same device with "No limits" applied) should be offered as a
  comparison for the delay of a phone that adds its own delay. Not built: the Compare feature of
  Test speed is the natural home for it.

## Data model

```
connection_check_results(            -- same columns as speed_test_results
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at INTEGER, finished_at INTEGER,
  mac_address TEXT, device_id TEXT NULL, device_name TEXT,
  label TEXT DEFAULT '', status TEXT,      -- complete | changed | fault
  provider TEXT,                            -- always 'icmp-echo'
  source TEXT,                              -- device | everyone | everyone-else | none
  protected INTEGER,                        -- 1 for a protected device
  profile_json TEXT,                        -- profile in force and its applied values
  summary_json TEXT,                        -- measured numbers, per-number statuses, verdict
  reported_json TEXT DEFAULT '{}'           -- unused
)
```

`summary_json` stays under the 16 KiB limit. It holds counts and statistics, not one entry per
probe: `probes` (planned, sent, received, lost, loss share and 95 percent range), `rtt` (min, mean,
median, p95, max, jitter), `duplicates`, `reordered`, `metrics` (the table the window shows: id,
set value, measured value, status, sentence), `verdict`, `verdict_text`, `notes`, `expected` (the
applied download impairment), `shaped`, `seconds`, `profile_unchanged`,
`service_rules`.

## API (all need the admin token and pass the saved-device check)

| Method and path | Body or query | Answer |
| --- | --- | --- |
| `GET /api/connection-checks` | `mac` optional | limits, labels, active checks, saved results (newest first, 50 for one device, 200 overall) |
| `GET /api/connection-checks/status` | `run` | one active check with its live counts |
| `GET /api/connection-checks/export` | `format=csv|json`, `mac` optional | the file text |
| `POST /api/connection-checks/start` | `mac_address`, `probes`, `label` | the new run (201) |
| `POST /api/connection-checks/cancel` | `run_id` | the ended run with `result` null |
| `POST /api/connection-checks/delete` | `result_id` | `{deleted: true}` |

Every body is checked field by field and unknown fields are refused. The speed test routes keep
working on speed test rows and runs only. A **run id** of the other kind answers 404 (run ids are
random and never shared). A **result number** is per table, so each route addresses its own table.

## UI

`connection-check.js` adds a **Check connection** button through the existing `connectedRowHooks`
list and builds one `<dialog>`: labelled by its title, focus moves to the title when the sections
change, focus returns to a live button when it closes, and live progress is announced in a polite
status region once per change of state (not once per probe). The dashboard's lock hook
(`privateDataLockHooks`) closes it and clears cached results. The result table has a row per
number with the set value, the measured value and a **text** status (never only a colour), and
scrolls sideways inside its own wrapper on a phone. Delete asks in the page's own dialog.

## Tests

* **Unit** (`tests/unit/test_connection_check.py`): Wilson interval against hand-computed values,
  burst inflation, expected ranges (duplicate and loss interplay, corruption), jitter and reorder
  simulation determinism, record statistics (loss, duplicates, reordering, round trip), the
  verdict table (every overall verdict, every per-number status, borderline, inconclusive,
  zero replies, `loss=100`), probes needed, export without secrets.
* **Manager and API** (`tests/unit/test_connection_check_api.py`): fake prober and fake clock;
  validation of every field, unknown fields, limits (one per device, two in total, shared with
  speed tests), protected device allowed, shared source not comparable, service rules withheld,
  profile changed at the end, device left the Wi-Fi, thread start failure, shutdown, cancel,
  token required for every route, speed test routes do not see check rows or runs, store
  migration (table added to an old database, idempotent), retention.
* **Prober** additions: duplicates, first-answer arrival order, payload size, bounded memory for
  300 probes, and the speed test's own behaviour unchanged.
* **Loop**: a second copy that arrives after the last first answer is counted (the settle wait),
  a cancel is seen within the listening step, the deadline stops a prober that never answers, and
  a change seen by the one-second watch ends the burst.
* **Kernel** (`tests/kernel/test_connection_check.py`, Docker on the colima profile): veth lab with
  the real renderer plan. Proves with a real netem queue that configured loss, delay, duplication,
  jitter and reordering are measured inside the range the model gives. Loss and duplication use a
  99.99 percent binomial interval (a wrong failure in about one run in ten thousand per assertion;
  the test reruns alone once, like the other statistical kernel test); delay, jitter and
  reordering use the model's tolerance. Negative controls: an unshaped path compared with a
  profile that is not applied is detected as `less` (the check can fail), 50 percent upload loss
  does not touch the replies, and a shared profile is not shaped.
* **Browser** (`tests/browser/connection_check.py`, desktop and phone width): open the window from
  a device row, start, see progress, see the verdict table, save, history, export, cancel, delete,
  protected device enabled, shared device not comparable, no page errors, no horizontal scroll.
  Registered in the local CI module list and the workflow.

## Integration points (so nothing is forgotten)

* `api/server.py`: one `ConnectionCheckManager` made with the server, the new reads and writes
  routed next to the speed test ones, the write paths added to `WRITE_PATHS` (token and
  saved-device check come with that). Shutdown needs no new code: a check lives in the speed test
  manager's run table and its shutdown stops and joins the check threads too.
* `config/storage.py` already calls `ensure_speed_test_schema`, which now creates both tables.
* `api/dashboard.py`: `connection-check.js` and `connection-check.css` are added to the assembled
  page (the architecture test fails for an asset nobody assembles). The GUI static budget is
  raised on purpose in the same commit.
* `api/dashboard.js`: the longer browser timeout of `/api/speed-tests/start` also applies to
  `/api/connection-checks/start`, because start reads the Wi-Fi status and the queues with the
  same bounded retries.
* `scripts/local-ci.sh`, `.github/workflows/wifi-recovery.yml` and `tests/unit/test_local_ci.py`:
  the browser module `connection_check` is listed like `speed_test_window`. The kernel module is
  found by unittest discovery.

## Risk class and rollback (pull request template)

* **Risk class B**: the dashboard page and its HTTP API. Network rules, the helper and the update
  path are untouched. **Household risk: low** (ten small pings per second to one device for at
  most 40 seconds).
* **Rollback**: revert the commit. It leaves the extra table `connection_check_results` in the
  database (bounded to 500 rows, ignored by older code). Nothing on the Pi's network changes.

## Risks

* **False verdicts.** The statistics are bounded by the 99 percent bar and by the tests, but a
  real Wi-Fi link adds noise. The allowances are named constants, the window shows the numbers and
  ranges next to every verdict, and "borderline" asks for a rerun instead of guessing.
* **Probe rate on a weak device.** 10 small pings per second for at most 40 seconds is far below
  any normal traffic. The count and rate are bounded on the server.
* **Built on PR 121.** This branch changes `api/speed_tests.py` (a refactor that extracts the
  target resolution and the thread start so both kinds share them) and adds files. It was first
  stacked on the PR 121 branch and was rebased onto main after PR 121 was squash-merged. The
  window follows the speed test window: its own in-dialog status region, and Delete asks in the
  page's own dialog (`confirmWorkflow`).
* **Panic-pinned files** are not touched. Nothing in `networking/helper.py`, `networking/access.py`,
  `panic_guard.py`, `dashboard_access.py`, `deadlines.py`, `diagnostic_records.py` or
  `diagnostic_privacy.py` changes, and `updates.py` is untouched.
* **Burst length.** The helper's client used to drop `loss_burst_packets` from the queue counters.
  Since the carry-forward fixes (PR 138) it keeps it, so the start check sees a burst length change
  and the check reads the burst length from the queue the helper reports. A reading without it
  counts as an unknown burst length and the result says so.
