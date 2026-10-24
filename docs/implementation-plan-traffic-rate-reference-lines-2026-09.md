# Implementation plan: live traffic speed reference lines

## Human review summary

Add editable ISP speed references to the live graph and show the selected
device's active default-queue bandwidth limit as a separate dashed line. Set
the download reference to 100 Mbit/s for this personal installation and leave
upload unconfigured until its speed is known. These are reference limits only:
the existing graph measures traffic at the Pi before profile queues, so it
cannot prove the speed a device receives. The key risks are showing a limit as
measured throughput and implying that the default device queue limit applies
to service-specific traffic. Keep labels explicit and do not change traffic
shaping. Existing uncommitted edits in the API, helper and Pi guide must be
preserved while integrating the chart work.

## Decisions and open questions

- Store ISP upload and download values as optional, editable rates in integer
  kbit/s. For this private home installation, initialize the download value to
  100,000 kbit/s (100 Mbit/s) and leave upload unset. New databases use this
  same starting value because this project currently targets this home line.
  Both values stay editable. Saving a value changes graph references only.
- Reuse the existing two-second traffic samples. Draw distinct dashed lines
  for ISP limits and the selected device's current effective profile limit.
  Include configured limits when scaling the graph so the reference lines
  remain visible even when traffic is low.
- Only show one device's profile limit when that device is selected. Several
  devices can have different active limits, so the current aggregate selection
  cannot be represented by one profile line. Continue showing the ISP plan
  maximum for aggregate traffic. Label it as the whole-line plan rate because
  other home devices can use some of that capacity.
- Obtain profile limits from a snapshot of the plan last successfully applied
  to the helper. Read it under the scheduler's existing lock. Do not call
  `build_plan()` for each chart poll because that can change runtime state and
  report a limit that has not been applied. A device profile limit is the
  default queue limit only. Service assignments and service quotas can route
  some traffic through other queues, so the graph note must say these limits
  are not represented by that line.
- Keep the current traffic trace and label it as traffic seen by JitterSim.
  Measuring traffic after shaping to prove the device's received speed is a
  separate feature and is not included here.
- For a profile with randomized bandwidth, use the current rolled limit from
  the last successfully applied plan. This shows the limit actually in force.
- Open design questions: none. The user has provided 100 Mbit/s as the
  download reference and upload can remain unset until known.

## Current behavior

1. The graph samples directional Pi firewall counters every two seconds and
   retains at most one minute in the browser.
2. The chart draws only measured upload and download rates. The API returns
   counters but no ISP settings or active profile limits.
3. Profiles can have separate optional upload and download bandwidth limits.
   Schedules, timers, progressions, data limits and group limits can change the
   effective profile without changing the device's saved default assignment.
   The scheduler already keeps the payload last applied under its lock.
4. The configured ISP reference is observational only. It must never be sent
   to the helper or added to the network plan.

## Proposed work

1. Add a small local traffic-settings table with optional integer upload and
   download rates in kbit/s. Add the 100,000 kbit/s download starting value
   only when the setting has never been created. Never overwrite an owner's
   saved value on restart or upgrade. Include values in the protected
   dashboard API. SQLite backup already copies the whole database, so add a
   backup round-trip check rather than changing backup code.
2. Add a small dashboard form for editing the ISP values. Show download as
   100.0 Mbit/s for this installation. Allow upload to remain blank. Use the
   existing admin-token checks and show save errors without interrupting graph
   sampling or network traffic.
3. Add a scheduler snapshot method that returns current device queue limits
   from the last successful apply while holding the existing lock. Extend the
   protected traffic response with those upload and download caps. Do not
   rebuild the plan for each two-second graph poll. Return an unavailable
   limit state when no plan is applied, such as after reset. Keep the previous
   applied caps visible after a failed apply.
4. Draw configured ISP and per-device default-queue caps as labeled dashed
   lines. Show per-device profile references only when one device is selected.
   Label that reference as the default device queue limit and explain that
   service-specific queue limits are not shown. When no plan is applied, say
   the profile limit is unavailable rather than implying it is unlimited.
   Keep the existing upload/download traffic lines and bounded sample history.
5. Update the README to explain the settings and state clearly that dashed
   lines are limits, while the traffic trace is measured at the Pi before
   shaping and is not a client speed test. Explain that the ISP plan maximum is
   shared with other household traffic and is not a live measurement of the
   bandwidth available to JitterSim.

## Data, security and deployment

- Store only two numeric local settings. Do not add user data, outside network
  calls or additional logs.
- Store rates as integer kbit/s. Accept 0.1 to 1,000 Mbit/s in 0.1 Mbit/s
  steps, using 100 to 1,000,000 kbit/s. Treat an empty value as unknown and do
  not draw a line for it. Reject booleans, non-integers, non-finite values,
  out-of-range values and malformed requests.
- Require the existing dashboard administrator token for reading and saving
  settings. Keep using the existing API listener and database.
- Use the existing startup schema initialization to add settings safely to
  installed databases. The installer should need no manual database command.
- The change does not apply, reset or otherwise alter traffic. Rollback is to
  revert the app update; an extra settings table can remain harmlessly in the
  local database.
- Preserve the current local changes in `src/jittersim/api/server.py`,
  `src/jittersim/networking/helper.py` and `deploy/raspberry-pi/README.md`.

## Verification and recovery

1. Test settings validation, the one-time 100 Mbit/s download starting value,
   upload left unset, editing, migration idempotence and restart persistence.
   Open a downloaded SQLite backup and confirm both saved values are present.
   Test missing and invalid admin tokens for settings read and write routes.
2. Test the graph data for no cap, independent upload/download caps, a saved
   profile, schedules, timers, progressions, device and group quota fallbacks,
   randomized bandwidth, service queue scope and aggregate device view.
   Verify polling reads the last successfully applied snapshot without
   rebuilding or mutating scheduler state. Cover reset, failed apply and
   missing/unlimited cap as distinct states.
3. Test chart scaling and reference-line visibility when the 100 Mbit/s ISP
   limit is far above observed traffic. Check that removing an optional limit
   removes its line and that the chart remains bounded to 60 seconds.
4. Run unit tests, focused lint, embedded JavaScript syntax checks and
   `git diff --check`. Confirm the pre-existing working-tree edits remain
   intact.
5. On the Pi, apply a 20 Mbit/s default download profile to one test device.
   Confirm the graph labels the 100 Mbit/s ISP reference and the 20 Mbit/s
   default-queue profile reference while ordinary clients remain outside that
   profile. Do not interpret the current traffic trace as the client's
   achieved 20 Mbit/s speed.

**Risk: medium.** The feature is read-only for packet handling, but it adds
persisted settings and relies on correctly identifying the last applied
default queue limits. A stale or incomplete reference could mislead the
operator. Keep the labels and explanatory note visible, verify resets and
failed applies and preserve the existing active plan during graph polling.

## Implementation and verification

The work is implemented. The dashboard saves graph-only ISP rates, the
traffic endpoint returns those settings with limits from the last applied
plan and the chart shows ISP and selected-device default-queue references.
The README explains that these lines are configured limits and the traffic
trace is measured at JitterSim before shaping.

Verification covers settings defaults, input validation, restart persistence,
schema re-creation, backup contents, protected API reads and writes, scheduler
snapshots, schedules, failed applies, dashboard markup and embedded JavaScript
syntax. The complete unit suite passed all 217 tests. Focused Ruff checks,
Python compilation, `node --check` on the embedded dashboard script and
`git diff --check` also passed.

The Raspberry Pi and a browser connected through its access point are not
available in this work session. After installing the update, confirm on the
Pi that a selected test device shows its active default-queue cap beside the
ISP reference. Treat that graph as configured limits plus traffic measured at
the Pi, not as proof of the device's achieved speed.


## Data scale follow-up, 24 October 2026

The chart now uses measured upload and download samples to choose a 1-2-5 axis
ceiling with 20 percent headroom and a 0.1 Mbit/s floor. Configured limits do
not stretch the axis. References above the axis retain their real values in
an above-chart label and a top-edge marker. Their legend inspection remains
available. In-range reference portions remain visible inside the plot.

The latest measured interval also appears as text with a collapsed table of
at most 30 intervals. The table refreshes with two-second samples without a
live region. Pause keeps a frozen snapshot and stops graph sampling. Resume
starts a fresh graph and counter baseline so no rate spans the pause.
