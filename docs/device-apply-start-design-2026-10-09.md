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
- Deleting a scoped device removes its row. When the last row goes, the state
  is a plain Reset and the next scheduler tick resets the helper.

Rollback compatibility: an older dashboard ignores the new table and sees a
normal Reset. On restart it resets the network again and its scheduler never
resumes saved settings. A rollback therefore stops the device-only start and
never resumes Everyone or other devices. The table is runtime state, not a
setting: export and import do not carry it, and an import keeps only rows whose
device ids still exist.

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
  control revision and a Reset counter. A device Apply queued behind a Reset
  or a Panic is refused after the lock and starts nothing.
- The helper apply is non-explicit. It never clears a Panic or outage latch
  and never resumes paused ad-blocking.
- Failure: the assignment stays saved, the scope is cleared, state becomes
  `error` with `last_action = reset`, and the next tick repeats the reset.
  No other saved setting resumes.
- Restart recovery with a scope applies the device-only plan again, not the
  whole saved plan.

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
