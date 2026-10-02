# Persistence mode for router outages

## Summary for review

Add a dashboard and terminal setting that controls whether an active
JitterSim experiment continues after the Pi loses its configured router
gateway. With the setting on, JitterSim keeps or restores the active
experiment after the gateway returns. With it off, a sustained gateway
outage stops the active experiment and keeps it stopped after the gateway
returns until the owner applies it again. Saved profiles stay saved.
Household access rules stay separate.

This first version covers router or gateway outages only. Pi restarts are out
of scope, as requested. Existing Pi restart recovery stays unchanged.

The main risk is confusing a router outage with a wider internet outage or a
brief missed check. The outage stop also needs coordination with scheduled
work, dashboard recovery and the existing terminal reset. Persistence defaults
off. Check the configured gateway every five seconds. Three failed checks
confirm an outage and two replies confirm recovery.

## Learning objective

Trace how an active network plan is applied and recovered, then design a
bounded outage state change that cannot race with manual or scheduled applies.
The main learning work is ownership of network state across gateway loss and
recovery. The dashboard control and documentation are supporting work.

## Current evidence

- `RuntimeScheduler` runs scheduled applies in the dashboard process and uses
  an apply lock to keep scheduled work from interleaving with manual applies
  and resets.
- The dashboard stores whether the last action was apply, schedule or reset.
  On dashboard startup it reapplies the saved configuration after a prior
  apply or schedule. This recovery behavior is intentionally unchanged in
  this version.
- The existing `jittersim reset` command can reset network rules outside the
  dashboard process. The scheduler notices that reset, so the outage stop
  must work with this existing path too.
- The privileged networking helper already reports the configured default
  gateway through `uplink-info`. It does not currently confirm that the
  gateway answers a reachability check.
- `NetworkApplier.reset()` removes JitterSim's experiment traffic rules. The
  helper also restores household access rules through a separate path. The
  new mode must not disable those household protections.
- Settings export and import list saved fields directly, so the new setting
  needs an explicit round-trip decision and older-file default.
- There is no separate system service for watching gateway reachability.
  The dashboard scheduler is already active while an experiment is running.

## Proposed scope

### 1. Define the setting and its saved state

- Add one global persistence-mode setting for experiment plans. Do not add a
  per-device or per-profile toggle in this first version.
- Keep the setting in the main saved configuration, separate from household
  access settings. Validate it in the saved-settings path, the dashboard API
  and the terminal commands.
- Default the setting to off for new and existing installations. Include the
  preference in settings export and import with off as the default for older
  files. Keep gateway health and the outage-stop latch local to the Pi.
- Do not change saved profiles, schedules or household access rules when
  upgrading.
- Show the selected mode and the current gateway state in the dashboard. Say
  plainly that off stops the live experiment after a confirmed gateway outage
  and requires Apply to start it again.
- Add terminal commands to show and change the mode and show gateway health.
  Use the same saved setting and checked helper path as the dashboard.

### 2. Detect a real gateway outage

- Check reachability of the configured default gateway on the Pi's uplink.
  Do not use a public internet address as the signal because an ISP outage
  would then look like a router outage.
- Use one bounded ICMP probe with a one-second timeout every five seconds.
  Three consecutive failures confirm an outage and two consecutive replies
  confirm recovery.
- Treat a missing route or an unavailable probe as unknown until the gateway
  has answered once. After that, count it as a failed check, since a router
  restart drops the Pi's Ethernet link and default route. Do not log client traffic, site names or credentials.
- Choose a probe that works with the existing privileged-command allowlist
  and test whether the target router responds to it. ICMP can be disabled by
  some routers, so this cannot be assumed from a local unit test.

### 3. Apply the mode without races

- Let the privileged helper own gateway checks and network changes. It already
  runs separately from the dashboard and applies or resets experiment rules.
- Keep the outage-stop latch in the helper's `/run/jittersim` state so it
  survives a helper restart but clears on a Pi reboot. This keeps general Pi
  restart recovery unchanged in this version.
- When the helper has no local state yet, wait for the dashboard to send the
  saved preference before starting gateway checks. This prevents the helper's
  temporary off default from overriding a saved on setting during startup.
- Use one helper lock for automatic outage changes, manual applies and resets.
  Scheduled applies must check the helper's stop latch before changing rules.
  Dashboard startup must read the helper state before it tries recovery.
- If reset fails, keep the latch set, report the error and retry at a bounded
  interval. Do not let a failed cleanup cause profiles to resume.
- In on mode, retain the active plan during a confirmed outage. After gateway
  recovery, rebuild and apply the plan active at that time once.
- In off mode, after the grace period, remove the active experiment plan,
  pause scheduled reapplication and save a stopped state. Do not erase the
  profile or schedule definitions.
- Keep the stopped state latched after gateway recovery. Require an explicit
  Apply before the experiment starts again. This avoids a scheduler tick
  silently undoing the stop.
- Serialize gateway-triggered changes with manual Apply, Reset and scheduled
  changes. Saving profile edits does not clear the latch. Turning persistence
  on while stopped does not resume a plan. Only explicit Apply after gateway
  recovery clears the latch.
- Let wall-clock schedules, scenario durations, quotas and other timers keep
  their current timing during an outage. After explicit Apply, rebuild the
  plan from the current time. Any run that has expired remains expired.
- Keep household access rules and the management connection active in both
  modes. Confirm that experiment reset does not remove either one.

### 4. Keep the first version narrow

- Do not change Pi restart recovery in this version. Applying a setting after
  a Pi restart continues to follow the current saved `last_action` behavior.
- Do not add per-profile controls in this slice.
- Add a roadmap note for a later choice about Pi restarts and any wider
  persistence behavior.

## Work sequence

1. Add the setting with an off default for existing databases and exported
   files. Expose it in the dashboard with clear saved and live state.
2. Add bounded gateway monitoring and serialized on/off behavior, including
   recovery, manual Apply and Reset interactions.
3. Add terminal commands to show and change the mode and show gateway health.
4. Update the deployment notes and roadmap. Document that Pi restart behavior
   remains unchanged in this first version.
5. Review the full change and prepare dedicated Pi acceptance steps. Do not
   deploy it to the live Pi or push it as part of implementation without
   separate authorization.

## Verification strategy

### Local behavior

- Check old database and settings-file loading, setting validation and
  dashboard reload.
- Simulate gateway reachable, unavailable, briefly unresponsive, route missing
  and probe failure states. Confirm that unknown and short failures do not
  stop an experiment.
- With the mode on, check that a gateway outage does not lose the selected
  plan and that recovery restores it no more than once.
- With the mode off, check that a sustained outage stops the experiment,
  pauses automatic reapplication and remains stopped after gateway recovery
  until explicit Apply.
- If the helper reset fails, check that the stop stays latched, scheduled work
  remains paused and the owner can recover explicitly.
- Race an outage transition with manual Apply, Reset, schedule changes and a
  mode change. Include terminal reset and dashboard restart. Confirm there is
  one final state and no stale plan is applied.
- Check mode and gateway-state terminal commands, including helper errors and
  a saved setting that is missing or invalid.
- Check settings export and import across current and older file versions.
- Confirm saved profiles, schedules, household access restrictions and
  management access remain intact.
- Confirm Pi/dashboard restart behavior remains the existing behavior and
  does not accidentally become part of the new mode.

### Deployment and Pi acceptance

- Check changed database initialization and the existing helper unit from a
  clean install and an upgrade. Verify that helper runtime state survives a
  helper restart and clears after a Pi reboot.
- Verify gateway detection on the supported router during a real restart and
  compare it with a separate ISP-only outage. Record any router that blocks
  the selected probe.
- In both modes, keep a test client connected through a router restart. Check
  experiment state before the outage, during it and after recovery. Confirm
  normal household access and the dashboard remain usable.
- Repeat with a brief packet loss event to verify the grace period avoids a
  false stop. Confirm manual Apply restarts the experiment after an off-mode
  stop.
- Automated checks cannot prove real router probe behavior. Record hardware
  acceptance separately from local test results.

## Security, scale and recovery

- Classify the change as high network risk because it changes live impairment
  rules and their automatic recovery.
- Keep probe targets limited to the configured gateway. Use finite timeouts,
  a fixed check interval and a small retry count so a long outage does not
  create a growing queue or repeated command load.
- Keep state transitions behind the existing authenticated dashboard and
  validated helper operations. Do not add arbitrary shell commands or expose
  new network controls to test clients.
- Store only the mode, gateway health state and bounded transition times or
  counts needed for recovery and diagnosis. Do not save browsing activity.
- Preserve a clear manual recovery path. If probing fails, report an unknown
  state and do not stop rules. If saved helper state is invalid, keep a stop
  latch and make bounded cleanup attempts. Never reapply profiles automatically
  from invalid state.
- Require review from someone familiar with JitterSim's network path and a
  Pi operator before live acceptance. Code completion does not prove router
  behavior.

## Confirmed design choices

- Persistence defaults off for new and existing installations.
- Check only the configured gateway with one ICMP probe every five seconds.
  Three failures confirm an outage and two replies confirm recovery. A missing
  route or unavailable probe is unknown until the gateway has answered, then
  counts as a failed check.
- The helper owns detection and network transitions. Its stop latch survives a
  helper restart in the same boot but clears on Pi reboot.
- Off mode stops live experiment rules and prevents automatic starts until the
  owner applies again. Saved profiles and schedules remain intact.
- Schedules and timed work keep using wall-clock time through an outage.
- The preference travels in settings export and import. Gateway health and the
  stop latch do not.
- Pi restart behavior stays unchanged in this version.
