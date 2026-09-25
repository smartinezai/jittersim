# Implementation plan: lab usability, safety and realism

## Reviewer summary

Close the everyday gaps found in the September review. The dashboard cannot
delete anything and shows profiles and devices as raw JSON, so it gains delete
actions, readable tables and editing. An SSH-friendly `jittersim reset` gives a
recovery path that does not depend on the dashboard. Profiles gain presets,
bursty loss and a jitter distribution, and jitter no longer reorders packets
by accident. Settings can be exported and imported as validated JSON so a class
demonstration can be repeated. The live traffic panel shows what JitterSim
itself dropped. Kernel tests move into the repository and the installer creates
the test Wi-Fi network the helper expects.

## Decisions and risks

- Deletes follow the existing POST action style (`/api/<kind>/delete`) and
  require the admin token. Deleting a profile that is still used by a device,
  group, schedule, active timer or progression, or limit is refused with a list
  of what uses it, because the database would otherwise delete those rules
  silently. Deleting a device also removes its schedules, timers, progressions,
  limits and group membership after a dashboard confirmation. Deletes that
  change the plan use the same live-apply rule as other changes, so they never
  undo an explicit Reset.
- Profiles and devices appear as tables with Edit and Delete. Edit loads the
  saved values into the existing form and keeps the ID fixed. The device form
  selects its profile from a list. IDs are derived from names until the ID is
  typed by hand. The token box moves to the top and Enter checks the token.
- `jittersim reset` records the reset in the database first, then asks the
  helper to reset. A running scheduler that finds a reset it did not perform
  pauses itself and resets once more, so a scheduled apply that raced the
  command cannot leave devices impaired. `jittersim status` shows the recorded
  state and whether the helper answers. The existing simulation output stays
  the default command.
- Saving a device or enabling a service or time limit is refused when devices
  and limits together would exceed the 15 traffic classes the kernel allows.
- Responses gain a Content-Security-Policy that allows the dashboard's own
  inline script and style by hash, deny framing and referrers, and stop
  advertising the Python version. Logging is configured so changes and errors
  reach the journal while routine polling stays quiet.
- Presets fill the profile form and are only saved when the user saves. Their
  values are approximations, not measurements of real networks.
- Bursty loss uses netem's Gilbert-Elliott model with the burst length in
  packets. The configured loss percentage stays the average. The jitter
  distribution uses the tables shipped with iproute2. When jitter is set without
  a bandwidth limit, the queue gets a rate far above any link so netem keeps
  packets in order. The Reorder setting still reorders deliberately. Existing
  profiles keep their values; the new fields default to the current behavior
  except for ordering.
- Export contains settings only: profiles, devices, groups, schedules, access
  rules and limits. It excludes usage, DNS history, timers and progressions.
  Import validates every item and every reference first, then replaces the
  settings in one database transaction. A failed import changes nothing.
- Drop and backlog counts come from `tc -s -j` for each device queue. They show
  packets JitterSim dropped or held, not what a device experienced.
- Kernel tests run only on Linux as root with `JITTERSIM_KERNEL_TESTS=1`. They
  use network namespaces and never touch the host's interfaces. A Docker script
  runs them on a Mac.
- The installer creates the `jittersim-ap` NetworkManager connection only when
  it is missing, using the generated SSID and password, and continues if
  creation fails. This could not be tested on a Pi and needs checking there.

## Work slices

1. Storage and API deletes, profile reference checks and capacity checks.
2. Dashboard tables, editing, profile selection, derived IDs, schedule and
   limit delete controls, and the token box.
3. The `reset` and `status` commands, external-reset handling in the
   scheduler and the installer's command link.
4. Security headers and logging.
5. Presets, bursty loss, jitter distribution and packet ordering, checked on a
   Linux kernel.
6. Settings export and import.
7. Drop and backlog counters in the helper, traffic API and live traffic panel.
8. Kernel tests and the Docker runner.
9. Test Wi-Fi creation in the installer, deployment guide, README and roadmap.

Each slice is committed separately with its tests.

## Verification and recovery

- Run the unit suite and the embedded JavaScript syntax check after every
  slice. Check dashboard changes in a browser.
- Run the kernel tests in Docker for every change to generated `tc` or `nft`
  commands, including bursty loss, ordering and queue counters.
- On a Pi, confirm `sudo jittersim reset` clears impairments while the
  dashboard runs and after it stops, confirm the test Wi-Fi comes up from a
  fresh install, and compare measured loss bursts with the configured values.
- Each slice can be reverted on its own. Imports can be undone by importing the
  previous export.
