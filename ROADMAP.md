# jittersim roadmap

This roadmap breaks the project into small deliverable slices. Each slice should leave the repository runnable or easier to inspect. The commit names are suggestions for focused local commits. They are intentionally smaller than a feature branch summary so each change can be reviewed and reverted independently.

## Milestone 0: project foundation

Goal: establish the project contract before touching live networking.

- [x] Create the private `jittersim` repository.
- [x] Add the project README and safety boundary.
- [x] Add the implementation plan and roadmap.
- [x] Add the Python package metadata.
- [x] Add formatting, linting and test commands.
- [x] Add a development-only fake networking executor.

Suggested commits:

1. `Document jittersim scope and architecture`
2. `Add the project roadmap and implementation plan`
3. `Add the Python package scaffold`
4. `Add development checks and ignore rules`

## Milestone 1: policy domain

Goal: represent a device impairment policy safely and predictably.

- [x] Define device, direction and impairment value objects.
- [x] Define profile and device-assignment models.
- [x] Validate ranges for delay, jitter, loss and rate.
- [x] Normalize IPv4 addresses and reject unsafe addresses.
- [x] Add useful validation error messages.
- [x] Add tests for boundaries and contradictory settings.

Suggested commits:

1. `Add device and impairment models`
2. `Validate profile limits`
3. `Validate device addresses`
4. `Test policy validation boundaries`

## Milestone 2: policy compiler

Goal: turn a safe high-level policy into an explicit network plan without changing the host network.

- [x] Define a command-plan data structure.
- [x] Compile device identities into nftables mark operations.
- [x] Compile upload and download settings into traffic-control operations.
- [x] Represent advanced ingress shaping through an `ifb` plan. Upload corruption runs on the
  `jittersim-ifb` device, fed from the test interface's ingress, so the Pi's own IP stack checks
  every damaged packet before it forwards it (PR 125). Upload delay, jitter, loss and rate stay
  on the uplink.
- [x] Include project-owned handles and names so cleanup is bounded.
- [x] Make compilation deterministic.
- [x] Add a text renderer for simulation and debugging.
- [x] Test that the same policy produces the same plan.

Suggested commits:

1. `Add the network command plan`
2. `Compile device marks`
3. `Compile egress impairment rules`
4. `Compile ingress impairment rules`
5. `Render plans for simulation`
6. `Test deterministic policy compilation`

## Milestone 3: safe apply and reset

Goal: apply a plan through a controlled adapter and recover from failure.

- [x] Add a fake executor for unit tests.
- [x] Add a subprocess executor that never invokes a shell.
- [x] Add explicit simulation mode.
- [x] Add bounded command timeouts.
- [x] Add apply and reset operations.
- [x] Prove repeated apply operations are idempotent on Linux.
- [x] Record command output and failures.
- [x] Add a global reset operation that removes only project-owned state.
- [x] Keep Panic reachable and bounded under load and failure: a stop that survives a helper
  restart, a household release that opens the firewall first and never rolls back to the
  restrictive rules, a reserved helper slot and one total Panic deadline (PR 95, 123, 136).

Suggested commits:

1. `Add the fake command executor`
2. `Add bounded subprocess execution`
3. `Add guarded apply and reset operations`
4. `Test guarded apply recovery`

## Milestone 4: device and profile storage

Goal: persist configuration without requiring a cloud service.

- [x] Add SQLite storage.
- [x] Store devices, profiles and assignments.
- [x] Store schedules, MAC rules and website rules.
- [x] Store optional device MAC addresses.
- [x] Store desired state separately from observed state.
- [x] Add configuration versioning.
- [x] Download a protected SQLite backup of settings and local observations.
- [x] Add import and export of non-secret JSON configuration.
- [x] Reject malformed imported configuration before persistence.

Suggested commits:

1. `Add SQLite configuration storage`
2. `Persist devices and profiles`
3. `Add configuration versioning`
4. `Add safe configuration import`
5. `Add configuration export`

## Milestone 5: web control plane

Goal: control the emulator from a Mac without a screen or keyboard attached to the Pi.

- [x] Add a small HTTP API.
- [x] Add authentication for state-changing operations.
- [x] Add device and profile listing.
- [x] Add apply and reset actions.
- [x] Add a simple dashboard served by the Pi.
- [x] Show desired state, observed state and last apply result.
- [x] Show connected devices with one-click profile assignment.
- [x] Edit schedules, MAC rules and website rules from the dashboard.
- [x] Add observed domains to a profile or the global block list.
- [x] Keep Apply and Reset feedback in a sticky action area.
- [x] Add a bounded history of admin actions without request contents.
- [x] Protect the deployed dashboard with HTTPS.
- [x] Keep the dashboard exempt from impairment.
- [x] Show anyone without the admin token only a neutral sign-in page and keep
  signed-in browsers signed in for 7 days.
- [x] Split the dashboard into tabs, fit it on a phone and guide a first setup.
- [x] Offer the connected devices in forms instead of typed addresses.
- [x] Give the dashboard a phone layout: a short header, Panic and the router Wi-Fi switch
  pinned to the bottom of the screen and the other sidebar controls in a sheet.
  (The switch has been read only since PR 87.)
- [x] Name the active general profile above Start and let the owner pick one there.

Suggested commits:

1. `Add read-only control API`
2. `Protect configuration writes`
3. `Add apply and reset endpoints`
4. `Test apply and reset endpoints`
5. `Add the first dashboard page`
6. `Show network state and apply errors`
7. `Record control-plane events`

## Milestone 6: intermittent behavior

Goal: create realistic changing conditions without making tests irreproducible.

- [x] Define normal, degraded and offline states.
- [x] Add fixed-duration outages.
- [x] Add repeating schedules.
- [x] Let schedules apply to a group or to everyone else.
- [x] Add timed scenarios for a device, a group or everyone else.
- [x] Move saved devices to the address their MAC address uses.
- [x] Add seeded random choices.
- [x] Reroll values and feature enablement at separate intervals.
- [x] Vary reroll timing and outage timing.
- [x] Add time-based and DNS-query-based progression sessions.
- [x] Stage per-device website blocking during progression sessions.
- [x] Add burst loss and correlated impairment presets.
- [x] Ensure only one scheduler owns transitions.
- [x] Stop schedules cleanly during reset and shutdown.

The next intermittent-behaviour work is detailed in the
[erratic connection plan](docs/implementation-plan-erratic-connections-2026-10-03.md).
Milestone 15 delivered the first part of that plan. Milestones 16 to 19 below record the
agreed direction and later feature ideas. They are planned work rather than available features.

Suggested commits:

1. `Add network condition states`
2. `Add scheduled state transitions`
3. `Add seeded randomness`
4. `Add intermittent profile presets`
5. `Stop schedules during reset`
6. `Test scheduler transitions`

## Milestone 7: Raspberry Pi deployment

Goal: run the system headlessly on the Pi while preserving a reliable recovery path.

- [x] Document Raspberry Pi OS setup.
- [x] Add systemd units for the dashboard and helper.
- [x] Add a configuration file for management and test interfaces.
- [x] Add interface safety checks at startup.
- [x] Restore the last applied plan or finish a reset after restart.
- [x] Document DHCP, NAT and the test Wi-Fi network.
- [x] Add an SSH reset procedure.
- [x] Announce the dashboard name after boot, keep the journal on disk and quiet the boot logs of
  the control firewall and AP units (PR 142).

Suggested commits:

1. `Document headless Pi setup`
2. `Add service configuration`
3. `Add systemd service units`
4. `Check configured test interfaces`
5. `Reconcile state after reboot`
6. `Document recovery procedures`

## Milestone 8: measurement and class demonstration

Goal: produce evidence that the selected profile changes observed network behavior.

- [x] Add counters for packets, bytes, drops and active state.
- [x] Show per-device upload and download rates in a live dashboard graph.
- [x] Show everyone else in the live graph.
- [x] Keep an activity log of network events that copies as Markdown.
- [x] Add a latency measurement page.
- [x] Add a small scripted test client.
- [x] Compare normal and impaired runs.
- [x] Capture screenshots or tables for the class report.
- [x] Document packet-level limits and IPv4 profile matching.
- [x] Test that the reset path works after a failed apply.

Suggested commits:

1. `Expose traffic counters`
2. `Add connectivity measurement helpers`
3. `Add repeatable demonstration script`
4. `Document measured behavior`
5. `Prepare the class demonstration`

## Milestone 9: byte quotas

Goal: switch a device or a selected observed service to a fallback profile after
it uses a configured amount of data.

- [x] Store device and service quota settings and usage.
- [x] Count upload and download bytes for assigned devices.
- [x] Group several observed DNS domains into one service quota.
- [x] Support manual, interval and daily resets.
- [x] Edit limits without discarding current usage.
- [x] Show usage, remaining data and reset controls in the dashboard.
- [ ] Verify quota behavior on the deployed Pi with real client traffic.

Quota matching uses observed domain names resolved to IPv4 addresses. Services
can use changing domains or shared delivery addresses. VPNs, encrypted DNS,
direct IP access and IPv6 can therefore reduce accuracy.

## Milestone 10: selective browser profiles and private control

Goal: shape named HTTPS sites in a managed Mac browser and control the Pi from
another location without exposing the dashboard publicly.

- [x] Add editable browser service assignments with direct hostname entry.
- [x] Keep other proxied sites on the device's ordinary queue even when a
  selected site shares its server address.
- [x] Bind the browser proxy to the isolated test network and reject private
  destinations.
- [x] Protect private dashboard reads and check helper health.
- [x] Serialize apply and reset, enforce the Linux queue limit and preserve
  foreign root queues.
- [x] Provision the test AP and document Mac browser setup.
- [x] Isolate test Wi-Fi clients from each other.
- [x] Document opt-in Tailscale Serve, access rules and removal.
- [ ] Verify network queues, Mac browser isolation, the AP and remote control
  on the actual Pi from another location.

The first browser release covers HTTPS CONNECT traffic sent through the proxy.
Native apps and browser paths that bypass it keep the ordinary device profile.
The test Wi-Fi provides IPv4 client access. Service quotas still use their
separate address-based matching.

## Milestone 11: household access point readiness

Goal: let the household join one JitterSim Wi-Fi while only chosen devices
receive network effects. Do not turn off the router Wi-Fi until this has been
verified on the target Pi under normal household load.

- [x] Keep unconfigured clients out of device-specific profile rules.
- [x] Give ordinary traffic a fair queue while selected devices are shaped.
- [x] Give everyone else one shared profile, except devices on an unaffected
  list.
- [x] Let a protected device open the dashboard with the admin token and no separate Allow this
  device step. Stopping protection removes only that access (PR 92, merged with PR 136).
- [x] Check Ethernet routing, AP interface and subnet compatibility before
  changing services during installation.
- [x] Avoid restarting an active AP during a routine software update.
- [x] Use WPA3 with AES for fresh installations and new manual Wi-Fi settings. Upgrades keep the
  existing security type, and copying the router's Wi-Fi keeps its personal security type when the
  Pi can verify that mode. (This replaced the first WPA2 rule.)
- [x] Restore the default Wi-Fi name (`TestNet`) from the dashboard or console without
  changing the password.
- [ ] Verify stronger Wi-Fi security and name recovery on the target Pi.
- [x] Use internet-only access for the first household trial. The isolated
  subnet continues to block the router's private services.
- [ ] Choose and implement a household network design if normal wired-network
  access and discovery are required.
- [ ] Measure Wi-Fi coverage, client count, throughput and reconnection on the
  actual Pi before migrating every device.
- [ ] Verify an unconfigured device remains online through repeated profile
  changes, helper restarts, dashboard restarts and a Pi reboot.

See the [household plan](docs/implementation-plan-household-access-point-2026-09.md).

## Milestone 12: device workflows and repeatable tests

- [x] Preserve unfinished forms across Apply, saves and background updates.
- [x] Add phone device cards and controls shared by Overview and activity events.
- [x] Preview upcoming rules with the scheduler's precedence and warn about overlaps.
- [x] Retain stale readings with clear connection and Pi health information.
- [x] Keep bounded settings history with preview and concurrent-edit protection.
- [x] Record named test sessions with settings, events, notes and measurement reports.
- [x] Duplicate profiles and provide manual copying when clipboard access fails.
- [x] Read router Wi-Fi state before presenting one verified switch. (Read only since PR 87.)
- [x] Install administrator-approved releases with progress, health checks and recovery.
- [x] Cut, check, sign and publish a release with one command, and update the Pi in one step from
  its terminal or from the Mac (PR 108, 110). An update is reported as done only when its
  install request completed (PR 122).
- [ ] Cut the first release, v0.2.0. The tooling is merged (PR 108, 109, 110, 114, 116, 122, 139)
  and no tag or GitHub release exists yet. What is left is the owner's: the one-time root install
  of current `main` on the Pi, the GitHub token, `scripts/release.sh 0.2.0` and the physical
  acceptance of the exact package (see Current remaining work).
- [x] Apply a saved profile to one device and start only that device while the network is
  reset (PR 96, 127, 131).
- [x] Let one device run its own profile while an Everyone profile runs (PR 149). The renderer
  leaves the device out of the shared mark and Linux packet checks cover it. It is refused while
  Everyone is saved but not running or while what runs is older than the saved settings.
- [x] Ask in an accessible in-page dialog before every destructive action (PR 141), speak each
  result once through one announcer and keep focus when lists refresh (PR 146, 152).
- [x] Exercise complete browser journeys in CI against an installed package.
- [ ] Verify radio changes, release installation and recovery on the actual Pi.
- [ ] Design a separate client measurement page if phone setup needs to be simpler.

Test Wi-Fi clients reach only a limited sign-in page. The private dashboard needs the admin
token plus saved device access on the test Wi-Fi. Release delivery installs only archives that a
trusted key signed. The online check uses a read-only GitHub token that
`sudo jittersim update set-token` saves on the Pi.

## Milestone 13: DNS failures and headless operations

- [x] Add a 0–100% DNS failure chance for test Wi-Fi with a safe 0% default.
- [x] Return prompt UDP and TCP network errors without changing the client's
  Unbound view or affecting management DNS.
- [x] Show the saved and applied chance in the dashboard and require a clear
  confirmation before applying 100%.
- [x] Add headless access, diagnostics, bounded logs and staged update commands.
- [x] Document caches, bypass paths and recovery commands.
- [ ] Confirm error behavior, DNS policy and service recovery on the target Pi.
- [x] Find a trusted online source if the Pi should check for private releases. The Pi asks
  GitHub for the newest signed release with a read-only token and stages it when a trusted key
  signed it and it is newer (`sudo jittersim update check --online`, PR 84).
- [x] Explore a separate browser feature for returning to a site's home page or
  a chosen destination. DNS cannot see a page path or browsing history. The controlled
  extension is `extensions/homepage-bounce` with live pairing (PR 84). Real test-device
  identity and physical recovery still need acceptance.

## Milestone 14: router outage persistence

- [x] Save one global setting for keeping active profiles through a router
  outage, defaulting to off.
- [x] Check the configured gateway with a bounded probe and require repeated
  failures before stopping profiles.
- [x] Keep OFF-mode stops in place after recovery until the owner applies again.
- [x] Show the saved mode and gateway state in the dashboard and terminal.
- [x] Include the preference in settings export and import.
- [x] Keep Pi restart behavior unchanged in this first version.
- [ ] Confirm ICMP behavior and router restart recovery on the target Pi.
- [ ] Decide whether a later version should stop profiles from resuming after
  a Pi restart.

## Milestone 15: reliable changing profiles

Goal: address the four existing weaknesses before adding richer faults.

- [x] Save the erratic connection implementation plan for review.
- [x] Complete independent plan review and obtain human feedback before
  production changes. The owner approved implementation on 3 October 2026 and an independent
  source review of Delivery A found two more problems that were fixed (the plan records both).
- [x] Advance outage choices independently from profile rerolls.
- [x] Define and verify changing behaviour for service lists, browser
  assignments and quota fallback profiles while preserving current priority.
- [x] Update compatible live queues without unintended queue loss or browser
  tunnel closures. Keep verified full replacement where it is required.
- [x] Offer a fresh pattern per session and a repeatable saved pattern while
  preserving existing profiles during upgrade.
- [x] Record bounded pattern choices without application contents.
- [ ] Verify limits, counters, protection, reset, restart and rollback.

Delivery A is merged (PR 84). The Linux live-update test
(`test_new_settings_keep_the_packets_a_queue_holds`) verifies counter continuity. Physical
client acceptance and independent expiry after a dashboard crash remain release gates.
Deliveries B to F are not started.

See Delivery A in the
[plan](docs/implementation-plan-erratic-connections-2026-10-03.md).

## Milestone 16: erratic connection patterns

Goal: create changing experiences across browsing, streaming and calls.

- [ ] Measure the existing burst-loss setting under browsing, streaming and calls.
- [ ] Add occasional long delays with optional persistence.
- [ ] Add bounded condition stages with variable durations and brief recoveries.
- [ ] Add short pauses followed by bounded bursts of traffic.
- [ ] Add Almost usable and Stop and go presets after their effects are verified.
- [ ] Check Linux feature support before applying a requested effect.
- [ ] Compare scenarios with similar average speed and loss using application
  completion, stalls, interruptions and recovery time.

See Deliveries B and C in the
[plan](docs/implementation-plan-erratic-connections-2026-10-03.md).

## Milestone 17: connection and application interruptions

Goal: let selected activity start successfully then become unreliable.

- [ ] Add a stable random outcome per selected browser connection.
- [ ] Add bounded pauses or closure after a connection age or byte threshold.
- [ ] Add deterioration under sustained load with bounded recovery.
- [ ] Add partial service faults using verified supporting hostnames.
- [ ] Add proposed ChatGPT and Claude presets for output arriving in bursts,
  responses that start then stop, slow sending and brief recovery windows.
- [ ] Add proposed Teams presets for one-way gaps, poor video and stalled
  screen sharing after verifying the actual media paths.
- [ ] Add reviewed targets for Google account sign-in, Search, Gmail, Drive
  and Docs plus an explicit whole-Google bundle with clear overlap handling.
- [ ] Add verified Netflix, Facebook and LinkedIn experiments for intermittent
  playback, media loading, uploads and synchronization.
- [ ] Add an approximate Almost finished mode based on connection traffic or
  time. Do not label it as a file completion percentage.
- [ ] Design an exact 90% to 99% interruption mode for controlled downloads
  with verified progress and an isolated transfer.
- [ ] Verify download resume, retries, parallel activity and client recovery.
- [ ] Preserve encrypted contents and report browser or native-client coverage
  honestly for every application preset.

An ordinary HTTPS tunnel cannot identify one file's size or completion
percentage. Exact progress and application-specific triggers require their own
reviewed source of information. HTTPS interception remains outside scope.

See Deliveries D and E in the
[plan](docs/implementation-plan-erratic-connections-2026-10-03.md).

## Milestone 18: later network experiments

Goal: retain the second-stage ideas for separate reviewed tickets.

- [ ] Fail new connections while established ones survive.
- [ ] Add intermittent one-way faults to ordinary device profiles.
- [ ] Delay or drop selected DNS lookups with clear cache and encrypted-DNS
  limits.
- [ ] Fail large packets while small transfers continue.
- [ ] Add selected UDP and browser-protocol interruptions with verified
  matching and fallback behaviour.
- [ ] Share an incident across a group with individual recovery variation.
- [ ] Explore delayed connection closure and idle-connection expiry.

These ideas need their own implementation plans and acceptance evidence.
Prioritise them after the earlier milestones demonstrate reliable recovery.

## Milestone 19: worsening with service usage

Goal: let a selected service become less reliable as one device uses it more.

- [ ] Add several worsening stages based on matching bytes or estimated active
  time without changing existing saved quotas.
- [ ] Add a Netflix example that increases buffering and bounded pauses as
  estimated active time grows.
- [ ] Add a finite usage window, a severity ceiling and explicit recovery and
  reset behaviour.
- [ ] Measure verified per-device service totals for direct and proxied traffic
  without double counting or counting protected clients.
- [ ] Add optional selection of the most-used service from an enabled candidate
  list with a stated measure and bounded lookback.
- [ ] Keep selected targets stable for a bounded interval so faults and retries
  cannot cause repeated switching or unbounded worsening.
- [ ] Show the selected service, usage estimate, matching limits and active stage.
- [ ] Verify profile conflicts, shared server addresses, unavailable measurements,
  concurrent devices, limits and automatic history deletion.

DNS queries are not page visits. Label this mode by measured service usage.
Keep Google account and shared sign-in targets separate from automatic
selection by default. One device's usage must not worsen another device's
connection through a shared service rule.

See Delivery F in the
[plan](docs/implementation-plan-erratic-connections-2026-10-03.md).

## Deliberate non-goals

These should remain outside the first class-project scope:

- HTTPS interception or certificate installation
- Application payload inspection
- Public cloud control
- Automatic discovery of every device on a home network
- Support for arbitrary router firmware
- Unbounded traffic logging
- Modifying networks that are not explicitly configured as the test network

The dashboard reads the primary Wi-Fi state of a Vodafone TG3442DE through its
local web interface and can copy its name and password to the Pi. Since PR 87 the
Pi helper refuses every router Wi-Fi write and the dashboard switch is read only:
turn the router's Wi-Fi on or off with the router's own button or page. JitterSim
does not inspect HTTPS pages or promise complete website blocking. The test AP
does not offer client IPv6 while device rules remain IPv4 based.

## Definition of done for the class project

The project is ready for presentation when it can:

1. Run on the Pi without a display or keyboard.
2. Serve a private dashboard reachable from the Mac.
3. Assign separate profiles to at least two test devices.
4. Demonstrate delay, jitter, packet loss and bandwidth throttling.
5. Demonstrate a scheduled outage and a seeded reroll.
6. Leave an unaffected management path.
7. Restore normal traffic with one reset action.
8. Explain what is measured at packet level versus application level.
9. Show tests and measured evidence.

## Router security matching and visible name recovery

- [x] Offer Wi-Fi name recovery as **Reset Wi-Fi name** on the Router and Wi-Fi page (the floating
  Quick actions panel was replaced by the sidebar).
- [x] Detect and preview the router band and personal security type.
- [x] Copy matching security with bounded recovery on failure.
- [x] Preserve the mode during name reset and installation upgrades.
- [x] Return the Pi Wi-Fi from a recovery name to the router name at boot after a copy that worked
  (PR 140).
- [ ] Complete physical client acceptance for every claimed supported mode.

## Current remaining work

Checked against `origin/main` at `f290ea9d` (PR 171) on 19 October 2026. Every unticked box in the
milestones above is one of these:

- Owner-run physical acceptance on the Pi (Milestones 9 to 15 and the router security section).
- An open design or decision: the household network design (Milestone 11), the separate client
  measurement page (Milestone 12), whether profiles should resume after a Pi restart
  (Milestone 14) and the two decisions about rollbacks in the
  [carry-forward list](docs/carry-forward-panic-batch-2026-10-10.md).
- Planned work that has not started: Milestones 16 to 19 (Deliveries B to F of the erratic
  connection plan) and the website list modes of the [planned features](docs/planned-features.md).
- Planned work in progress: the draft pull requests listed below (Check connection, security follow-ups, QA follow-ups).
- Requested on 23 October 2026 and not started (see [planned features](docs/planned-features.md)):
  show which device the browser in use is in the Devices tab, a speed test that runs from the
  dashboard alone, a global block list larger than 100 names, a quick button to reset the Pi
  Wi-Fi name, and protected devices that the global block list does not affect. After v0.2.0: a
  dashboard redesign with components (see [planned features](docs/planned-features.md)).
- The v0.2.0 release itself (Milestone 12), described below.

Hosted CI cannot start jobs (billing limit). Every pull request is gated by
`scripts/local-ci.sh` run on its exact head commit (see [local CI](docs/local-ci.md)).

Merged since the last roadmap reconciliation on 17 October 2026: PR 112 (named install button,
notes and recovery copy), 117 (wheel build lock), 138 (carry-forward fixes of the Panic batch,
`8c3f8171`), 139 (release tooling fixes), 141 and 146 (in-page questions, one announcer), 142
(Pi boot robustness), 143 (stability pass), 147 and 148 (removals), 152 (QA sweep), 154 (release
test image) and 171 (three PR 162 review items). `PANIC_APPROVED_SOURCES` has seven entries. The newest is for PR 138 and main's three
hash-pinned files match it.

Merged on 18 and 19 October: PR 111 (signed notes in the update status, check-only manual checks),
121 (device speed test), 149 (a device runs its own profile beside Everyone), 150 and 157 (router
safety audit round 2 and follow-ups), 151 (address changes), 155 (read route table), 159 (security
audit), 160 and 169 (boot Wi-Fi look), 162 (QA leftovers and flaky tests), 164 (soak harness and
resource fixes), 163 (write route table), 165 (speed test follow-ups), 166 (mutation testing), 167
(property tests) and 168 (interrupted apply cleanup).

Open pull requests on 19 October 2026 (all drafts, PR 163 and 165 merged during the day):

| PR | What it does | State |
| --- | --- | --- |
| 161 | A **Check connection** button that sends probes to a device and compares loss and delay with its profile (replaces PR 153) | Draft |
| 170 | Finish the follow-ups of the PR 159 security audit | Draft |

The first release, v0.2.0, is being prepared. `pyproject.toml` already says `0.2.0`, but no tag, no
GitHub release and no reserved version exist yet (`docs/releases/registry.json`). The release tooling
is merged. The owner's steps, in order: install current `main` on the Pi once with the root installer
(a Pi that runs an older development build refuses the signed packages), save the read-only GitHub
token on the Pi, connect the Mac to the dashboard, run `scripts/release.sh 0.2.0`, install and test
the exact package on the Pi and approve the release (see the
[owner runbook](docs/release-runbook-2026-10-16.md) and the
[draft release notes](docs/release-notes-0.2.0-draft.md)). The owner approves the release itself.

Other places to look:

- [Planned features](docs/planned-features.md) and [planned fixes](docs/planned-fixes.md) hold the
  status of requested work and of known problems.
- The physical checks that remain are in the [acceptance checklist](docs/pi-acceptance-checklist.md).
- [Feature proposals](docs/feature-proposals-2026-10-09.md) lists thirty researched ideas that are not
  started work.
- The [documentation index](docs/README.md) lists the older plans and marks each as current,
  historical or superseded.
