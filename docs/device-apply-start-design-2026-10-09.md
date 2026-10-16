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
| Everyone running | The device leaves Everyone and runs its own profile. Everyone keeps running for every other device. See "Everyone running: a device beside Everyone". |
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
- The trigger only sees writes made after it exists. A database that has the
  scope table but not yet the trigger may hold rows that an older dashboard left
  behind a Reset, with the state later marked unknown by a save. On that first
  upgrade every scope row is discarded. The network stays a Reset, so restart
  recovery resets it before anything starts.
- Deleting a scoped device removes its row. When the last row goes, the still
  running scheduler resets the helper on its next tick. A failed reset stays
  pending: the scheduler keeps retrying with growing waits, up to 8 tries, and
  records `error` until it works.

Rollback compatibility: an older dashboard ignores the new table and sees a
normal Reset. On restart it resets the network again and its scheduler never
resumes saved settings. A rollback therefore stops the device-only start and
never resumes Everyone or other devices. When this version comes back after an
older dashboard ran, the trigger, the first-upgrade discard and the startup
check keep stale rows from starting again.

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
  dashboard records `error` with the marker `device-only cleanup failed`. It
  retries only the reset, with bounded waits, whether or not the scheduler runs.
  No other saved setting resumes.
- The marker alone means the cleanup is still owed. A settings save in between
  marks the state unknown but does not cancel it. Each retry captures the Panic
  generation first and records its result through the Panic fence, so a newer
  Panic result is never overwritten. Shutdown waits for a running retry, up to
  the shutdown time limit.
- If the Pi does not answer whether Panic or an outage latched a stop, Apply is
  refused before saving.
- Restart recovery checks the scope before the last result. A scope applies the
  device-only plan again, even after a scheduled failure. A pending cleanup
  resets again at once. If that fails, the marker stays and the bounded retries
  start. Otherwise the earlier rules apply.

## Dashboard

- The runtime state carries `device_only` (scoped device ids). The status
  line says the start covers only those devices and that Start resumes
  every saved setting.
- The sidebar shows Everyone as paused and lists only the scoped devices.
- The Everyone picker keeps a pending pick when only the running flag changes.
  It still resets when the saved Everyone profile itself changes.

## Everyone running: a device beside Everyone

The owner approved this case on 4 October 2026. While Everyone runs, applying a
profile to one connected device saves the assignment and starts it for that
device at once. Everyone keeps running with the same profile, the same shared
limit and the same service lists for every other device. The device runs the
profile it would run without Everyone: its scenario, timer, progression,
schedule, group profile or device profile, then its data-limit fallbacks. Its own
service limits and browser service rules stay.

Behaviour:

| State when Apply is pressed on one device | Result |
| --- | --- |
| Everyone running | The device leaves Everyone. Other saved devices stay under Everyone. |
| Everyone saved but not running: never started, a failed apply, or the applied plan has no Everyone or a different Everyone profile than the saved one | Refused before saving (409). Start would run Everyone for every device, which this Apply must not do. Choose Start, Reset network first or stop Everyone. |
| Panic or outage stop latched, or the Pi does not say | Refused before saving (409), as after a Reset. |
| Device protected | Refused before saving, as before. |
| More than 15 classes with Everyone, the exceptions, their service limits and the service lists | Refused before saving (409) and the earlier exceptions stay. Service limits count even when the plan is checked without DNS. |
| The helper fails to apply | The assignment stays saved. The device is not an exception and the earlier ones stay. The dashboard applies the plan that ran at once, not the saved settings, because a failed apply can remove the rules and the new profile may be what failed. The scheduler keeps trying if that fails too and later plans the saved settings as for any saved change. A Panic that lands meanwhile wins: the exceptions are put back only through the Panic fence. |

How long the device stays beside Everyone:

- Start, Apply to everyone and Stop for everyone end every exception once the
  helper accepted their plan. Reset network and Panic end them at once. They are
  whole-scope actions, so every device follows them. Stop for everyone then plans
  the saved devices as it always did, which includes the device that was the
  exception.
- Deleting the device removes its exception and nothing else changes. Everyone
  covers the device again on the next apply. Nothing paused resumes.
- A saved change, a scheduled apply and a restart of the dashboard keep the
  exceptions. A scheduled apply never adds a device. A scheduled apply of a plan
  without Everyone, for example after a failed Stop, ends them once it succeeds. A
  failed scheduled apply keeps them.
- A whole-scope action ends them only after the helper accepted its plan. A
  failed Start, Apply to all or Stop for everyone keeps them and applies the plan
  that ran again with its randomization state, outage deadlines and sessions, so
  the record, the retry, the next tick and the running policy agree.
  The usage of the devices is sampled before the counters restart.
- Traffic progressions sample and advance only for the devices that run beside
  Everyone. The others stay paused.
- Importing settings replaces the saved devices, so every exception goes.
- Everyone's own roll, outage and website choices are unchanged by an exception.

Representation:

- New table `everyone_exceptions(device_id PRIMARY KEY REFERENCES devices(id)
  ON DELETE CASCADE)`, created with `CREATE TABLE IF NOT EXISTS`, so the
  migration is automatic and idempotent. It is runtime state and never exported.
- The rows count only while an Everyone profile is saved and the last action is
  `apply` or `schedule`. Anything else reads as no exceptions.
- Every `update_runtime_state` call ends them unless it passes
  `keep_everyone_exceptions`. A saved change and a scheduled result keep them
  through the new column `runtime_state.exceptions_kept`, which only the
  statement that sets it can see.
- The database enforces this too. A trigger deletes the rows on any write to the
  state columns that did not set `exceptions_kept`, so an older dashboard after a
  rollback ends them with its first state write.
- Choosing, changing or stopping Everyone does not delete them by itself. The
  write that records the helper's acceptance does. A failed Apply to all, Stop
  for everyone or Start keeps the record, so it never changes the running policy.

Rollback compatibility: an older dashboard ignores the table. On restart it
applies Everyone for every device, which ends the exception on the network and in
the database with its first state write. When this version comes back the rows are
gone, so nothing starts again by surprise. The new column has a default and older
code never reads it.

Plan, helper and kernel:

- Everyone keeps mark 1 and the service lists keep the marks they have under
  Everyone alone, so a device that leaves Everyone never moves them or the queues
  of the clients that stay. Exception devices and their service limits and browser
  service rules take the marks after the lists. Everyone, the lists and the
  exceptions share the 15 classes. A saved device cannot use the id
  `general-profile` beside Everyone: it is Everyone's queue identity.
- The plan codec accepts devices beside Everyone, never lets a device or service
  rule take mark 1 and still refuses everyone-else beside Everyone. No helper
  source changed: the helper and the applier only follow the plan they receive. A
  helper that was not restarted after an update refuses the new plan and the
  apply fails as any other failed apply does.
- The rules skip the exception addresses in both directions. The upload mark,
  the Everyone website allowlist and the Everyone outage match only other source
  addresses. Downloads are identified for everyone except the exception
  addresses, and IPv6 downloads are identified as before. The device's own
  marks, counters, queues, website filters and outages are the normal device
  rules.
- An Everyone outage cannot name the device's IPv6 address, so while Everyone is
  in an outage all IPv6 uploads are dropped, as they already are when a service
  list guards the outage rule. IPv4, which JitterSim shapes, stays connected for
  the device. The supported test Wi-Fi offers no client IPv6 (`ipv6.method
  disabled`, see deploy/raspberry-pi/README.md), so this never meets a client in
  the supported setup. A saved MAC address in the plan would remove the limit.
- The browser proxy gives a device beside Everyone its own queue and website
  rules and gives every other client Everyone's.
- The generated rules without exceptions are the rules they always were.
- Packet checks (`tests/kernel/test_everyone_exception.py`) cover uploads,
  downloads, loss, an Everyone outage, a Protected client, two devices, counters
  and a switch in both directions while pings run.

Dashboard: the runtime state carries `everyone_exceptions`. The connected clients
list shows the device's own profile and says it runs beside Everyone instead of
"Same as everyone". The sidebar lists the
device with its own profile and the other devices with Everyone's, and the status
line says how many devices run their own profile. The Everyone card explains it.

## Not covered

- A service list covers every affected client, so listed traffic of an exception
  device uses the list's profile, as it does for any device. Its listed
  downloads are not counted as Everyone's traffic. The device's own counter
  counts them.
- Device speed tests, the traffic page's per-device view and per-device limits
  other than data limits are unchanged by this change.
- A running helper needs no new capability flag: that would change a Panic
  consumer. A later change can add one with its own approval.
