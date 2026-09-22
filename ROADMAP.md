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
- [ ] Represent advanced ingress shaping through an `ifb` plan if later measurements require it.
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
- [ ] Prove repeated apply operations are idempotent on Linux.
- [x] Record command output and failures.
- [x] Add a global reset operation that removes only project-owned state.

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
- [ ] Store desired state separately from observed state.
- [ ] Add configuration versioning.
- [ ] Add import and export of non-secret JSON configuration.
- [ ] Reject malformed imported configuration before persistence.

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
- [ ] Add an event log.
- [ ] Keep the dashboard exempt from impairment.

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

- [ ] Define normal, degraded and offline states.
- [x] Add fixed-duration outages.
- [x] Add repeating schedules.
- [x] Add seeded random choices.
- [x] Reroll values and feature enablement at separate intervals.
- [x] Vary reroll timing and outage timing.
- [x] Add time-based and DNS-query-based progression sessions.
- [x] Stage per-device website blocking during progression sessions.
- [ ] Add burst loss and correlated impairment presets.
- [ ] Ensure only one scheduler owns transitions.
- [x] Stop schedules cleanly during reset and shutdown.

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
- [ ] Add a boot-time cleanup or reconciliation step.
- [ ] Document DHCP, NAT and the test Wi-Fi network.
- [ ] Add an SSH reset procedure.

Suggested commits:

1. `Document headless Pi setup`
2. `Add service configuration`
3. `Add systemd service units`
4. `Check configured test interfaces`
5. `Reconcile state after reboot`
6. `Document recovery procedures`

## Milestone 8: measurement and class demonstration

Goal: produce evidence that the selected profile changes observed network behavior.

- [ ] Add counters for packets, bytes, drops and active state.
- [ ] Add a latency measurement page.
- [ ] Add a small scripted test client.
- [ ] Compare normal and impaired runs.
- [ ] Capture screenshots or tables for the class report.
- [ ] Document limitations such as packet-level loss and IPv4-only support.
- [ ] Test that the reset path works after a failed apply.

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

## Deliberate non-goals

These should remain outside the first class-project scope:

- HTTPS interception or certificate installation
- Application payload inspection
- Public cloud control
- Automatic discovery of every device on a home network
- Support for arbitrary router firmware
- Unbounded traffic logging
- Modifying networks that are not explicitly configured as the test network

The Vodafone TG3442DE Wi-Fi switch remains a manual action because the project
does not have a supported router control API. JitterSim also does not inspect
HTTPS pages or promise complete website blocking.

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
