# Apply a profile to its selected device: design addendum

Addendum to the owner-approved plan "Apply a profile to its selected device"
(4 October 2026). It fixes the concrete design before the code change.

## Reproduced failure

With low saved for Everyone, Reset, then Apply slow-test to the Air on the
Connected clients page, the server saves the Air assignment and answers
"Saved. The network is reset, so choose Start". Nothing starts. Start then
compiles the whole saved plan, where the saved Everyone profile wins over
every device, so low runs for everyone and the Air never gets slow-test.

## Behaviour after this change

| State when Apply is pressed on one device | Result |
| --- | --- |
| Stopped by Reset, no latch | The device profile starts for that device only. Everyone, other saved devices, Everyone else, service lists and ad-blocking stay paused. |
| Already a device-only start | The device joins the running device-only set. Nothing else resumes. |
| Running normally without Everyone | Unchanged: the assignment is saved and the running saved plan is applied again. |
| Everyone running | Refused before saving (409). The answer names the running Everyone profile. See "Not covered". |
| Panic or outage stop latched | Refused before saving (409). Only Start or Apply to everyone may clear that stop. |
| Device protected | Refused before saving, as before. |

Save stays save-only. Start (`/api/apply`) and Apply to everyone stay the
explicit whole-scope actions and end a device-only start. Everyone Stop while
the network is stopped now only saves the removal: it no longer starts every
saved device profile as a hidden consequence.

## Representation

A device-only start is "stopped except these devices":

- `runtime_state.last_action` stays `reset`. `observed_state` is `applied`
  (or `applying` and `error` during and after a failed start).
- New table `device_start_scope(device_id PRIMARY KEY REFERENCES devices(id)
  ON DELETE CASCADE)` lists the devices that run. It is created with
  `CREATE TABLE IF NOT EXISTS`, so the migration is automatic and idempotent.
- The scope is honoured only while `last_action` is `reset`. Every runtime
  state write clears it unless the write is part of a device-only start. So
  Reset, Panic (normal access), the Panic undo of an overtaken apply, the CLI
  reset, Start and Apply to everyone all end the device-only start.
- The database enforces this too. A trigger on `runtime_state` deletes the
  scope on any write that is not a reset, that records `resetting` or `reset`,
  or that records a failure other than a scheduled one. That covers writes from
  an older dashboard. At startup, rows paired with a completed plain Reset are
  discarded before recovery.
- Deleting a scoped device removes its row. When the last row goes, the still
  running scheduler resets the helper on its next tick. A failed reset stays
  pending: the scheduler keeps retrying with growing waits, up to 8 tries, and
  records `error` until it works.

Rollback compatibility: an older dashboard ignores the new table and sees a
normal Reset. On restart it resets the network again and its scheduler never
resumes saved settings. A rollback therefore stops the device-only start and
never resumes Everyone or other devices. When this version comes back after an
older dashboard ran, the trigger and the startup check keep stale rows from
starting again.

The table is runtime state, not a setting. The settings export does not carry
it, but a full database backup copies it like any other table. An import
replaces the saved devices, so its rows go with them. The scheduler then ends
the device-only start on its next tick.

## Compiler precedence and shared limits

The plan builders take an explicit `device_scope`. With a scope:

- The saved Everyone profile is skipped. Only scoped devices are compiled,
  each with its normal device precedence (scenario, timer, progression,
  schedule, group profile, device profile, then data-limit fallbacks).
- Everyone else (profile, schedule and scenario), service lists and browser
  service rules of devices outside the scope are left out.
- A scoped device's own service and time limits and browser service rules stay.
- A shared group data limit counts only the scoped members. The limit's
  fallback applies to them when the saved usage already reached it.
- Global household rules (MAC policy, global website rules, access plan) and
  protected clients stay in every plan, as they do for Start and Everyone.

## Protection, timers and ordering

- A protected device is refused as before. Protected MACs stay in the plan.
- The scheduler keeps running a device-only start, so timers, schedules,
  progressions, address moves and limits of the scoped devices keep working.
  `record_schedule_result` keeps `last_action = reset` while a scope exists.
  A scheduled apply never widens the scope.
- Intent is captured before the shared lock: the Panic generation, the helper
  control revision, a counter of Reset and Panic requests and how many are still
  running. A device Apply that arrives while a Reset or Panic runs, or that waits
  behind one, saves nothing and starts nothing. Panic generation and helper
  revision are checked again just before saving on both paths. The save itself
  commits under the same fence Panic uses, so Panic cannot land between the
  last check and the commit.
- The helper apply is non-explicit. It never clears a Panic or outage latch
  and never resumes paused ad-blocking.
- Before the device joins, the scoped plan is compiled with the new device. A
  plan that cannot run, for example a 16th device, is refused and nothing is
  saved. A start refused before the helper ran puts the previous scope back.
- Failure at the helper: the assignment stays saved, the scope ends and the
  helper is reset at once. It records a clean Reset. If that reset fails, the
  dashboard records `error` and retries only the reset, with bounded waits,
  whether or not the scheduler runs. No other saved setting resumes.
- If the Pi does not answer whether Panic or an outage latched a stop, Apply is
  refused before saving.
- Restart recovery checks the scope before the last result. A scope applies the
  device-only plan again, even after a scheduled failure. A pending cleanup
  resets again. Otherwise the earlier rules apply.

## Dashboard

- The runtime state carries `device_only` (scoped device ids). The status
  line says the start covers only those devices and that Start resumes
  every saved setting.
- The sidebar shows Everyone as paused and lists only the scoped devices.
- The Everyone picker keeps a pending pick when only the running flag changes.
  It still resets when the saved Everyone profile itself changes.

## Not covered

A device exception while Everyone is running needs the general-profile
renderer to exclude the device's address from the shared mark in both
directions. That changes kernel rules and needs Linux packet checks. Until
then Apply is refused with a clear answer while Everyone runs, and nothing is
saved silently.
