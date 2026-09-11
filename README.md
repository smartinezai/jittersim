# jittersim

`jittersim` is a small network-emulation appliance for a Raspberry Pi. It creates a deliberately inconvenient but controlled network so that applications can be tested against delay, jitter, packet loss, outages and bandwidth limits.

The main use case is a private classroom or home lab. A Mac or another computer opens a web dashboard on the Pi. The Pi acts as the gateway for a separate test network and applies a different impairment profile to each test device.

> **Safety boundary:** Use this only on a network you own or are explicitly authorized to test. The first release is designed for an isolated lab network. It must not be exposed to the public internet or installed as an unreviewed rule on a shared production network.

## Project status

The project now has validated device and profile models, a deterministic policy compiler, a simulation command and a bounded networking command adapter. It does not yet apply rules to a Raspberry Pi. The next milestone is persistence and a controlled apply/reset workflow before adding the dashboard.

## Why this project exists

Many applications work well on a fast reliable connection but behave poorly when a connection is slow or unstable. Developers often test only with a single fixed delay. Real networks can have several problems at once and those problems can change over time.

`jittersim` is intended to make those conditions easy to reproduce:

- Add a fixed delay to traffic.
- Add random delay variation, also called jitter.
- Drop a configurable percentage of packets.
- Limit upload and download rates.
- Reorder, duplicate or corrupt packets where the Linux traffic-control stack supports it.
- Alternate between normal, degraded and offline states.
- Apply different policies to different devices.
- Record what policy was active and when it changed.
- Restore normal traffic quickly with an emergency reset.

## Example scenario

```text
Test Wi-Fi network
│
├── Alice's laptop  → 3000 ms delay, 700 ms jitter, 10% packet loss
├── Bob's phone     → 500 ms delay, 5 Mbit/s limit, short outages
└── Other devices   → normal traffic
```

The dashboard could represent this as:

```text
Scenario: Terrible Wi-Fi

Alice's laptop
  base delay:       3000 ms
  jitter:            700 ms
  packet loss:        10 %
  download limit:      2 Mbit/s
  upload limit:        1 Mbit/s

Bob's phone
  base delay:        500 ms
  jitter:            250 ms
  packet loss:          5 %
  outage duration:     10 s
  outage interval:   2–5 min
```

Packet loss is a packet-level behavior. It does not mean that exactly the same percentage of HTTP requests will fail because one request may contain many packets. An application-level request dropper would require a separate proxy and would not cover every protocol. The core project therefore describes this feature as packet loss.

## Network layout

The Pi must be in the traffic path. A recommended first setup is:

```text
Existing router ── Ethernet ──> Raspberry Pi
                                  │
                                  ├── test Wi-Fi access point
                                  ├── test wired network, if needed
                                  └── dashboard for the Mac
```

Devices that remain connected directly to the existing router will bypass `jittersim`. The dashboard should be reachable from a management address that is exempt from impairments so that the operator cannot accidentally lock themselves out.

The first version targets IPv4 on an isolated test network. IPv6 support is a separate milestone. Until it is implemented, the test network should disable IPv6 or explicitly route it outside the experiment so that traffic is not silently left unmodified.

## How the traffic engine works

Linux already provides the low-level mechanisms needed by this project:

- `tc netem` provides delay, jitter, loss, rate limits and other packet impairments.
- `nftables` identifies packets by source or destination address and assigns a packet mark.
- Traffic-control classes use those marks to select the device's profile.
- Intermediate functional block devices, commonly called `ifb` devices, allow the project to shape incoming traffic as well as outgoing traffic.

The application should not rebuild the whole network configuration for every packet. Instead it should compile the desired device policies into a small set of rules and update them when a profile changes. A single reconciler owns changes to the kernel state so two dashboard actions cannot apply conflicting configurations at the same time.

## Planned architecture

```text
Browser on Mac
      │ HTTP on private management network
      ▼
Web dashboard and API
      │ validated policy commands
      ▼
Privileged networking helper
      │ nftables and tc operations
      ▼
Linux forwarding, marking and traffic control
      │
      ▼
Test devices and upstream router
```

### Control plane

The control plane will be a small Python web service. It will store device names, reserved addresses, profiles and schedules in SQLite. It will validate all values before sending a request to the networking helper.

The dashboard will initially use plain HTML and JavaScript served by the Python service. A larger frontend framework is unnecessary for the first milestone and would add build work without improving the networking experiment.

### Data plane

The data plane will use Linux networking commands through a narrow adapter. The adapter must:

- Accept structured values instead of arbitrary shell text.
- Validate interface names, addresses, percentages, rates and time limits.
- Use subprocess argument arrays rather than a shell command string.
- Support a dry-run mode for development.
- Capture command output for diagnostics.
- Apply changes in a recoverable order.
- Restore a known normal state when requested.

The web service should not run as root. A later milestone will add a small privileged helper with a Unix-domain socket and a restricted command vocabulary. During early development the engine can use a fake executor so policy logic can be tested without changing a real network.

## Profiles and state

A profile describes a network condition. A device assignment says which profile applies to a device. A schedule changes the active state over time.

The conceptual model is:

```text
Device
  name
  reserved IPv4 address
  optional observed MAC address
  active profile

Profile
  download impairment
  upload impairment
  packet delay
  jitter
  packet loss
  bandwidth limit
  random seed

Schedule
  normal duration
  degraded duration
  offline duration
  repeat count or end time
```

Device addresses should be stable through DHCP reservations. MAC addresses are useful for discovery but should not be the only identity because modern phones and laptops may use randomized MAC addresses.

## Safety requirements

The following are part of the product rather than optional polish:

- Bind the dashboard to a private management address.
- Require authentication before changing traffic rules.
- Do not provide an internet-facing administration endpoint.
- Keep the management device and the Pi's own health checks exempt.
- Limit every numeric input to a safe range.
- Provide a visible global reset control.
- Provide a command-line reset path over SSH.
- Store the last known desired configuration.
- Reconcile or clear stale rules after restart.
- Log every apply, reset and failed operation.
- Warn when the selected interface is not part of the test network.

If the helper or dashboard stops unexpectedly, the default recovery behavior should be to remove `jittersim` rules and leave ordinary forwarding available. A deliberate offline profile is allowed only while the service is healthy and the scenario is active.

## Raspberry Pi target

The Raspberry Pi 3 Model B+ is suitable for an initial classroom prototype with a small number of devices. Its main constraints are throughput, shared USB bandwidth and the limitations of using one small board as both a router and a wireless access point.

Use a Pi 4 or Pi 5 if the project later needs many clients, high-speed links, stronger wireless performance or more monitoring services. The software should keep the network engine independent of the web dashboard so it can move to stronger hardware without changing the profile model.

Recommended first hardware setup:

- Raspberry Pi 3 Model B+
- Raspberry Pi OS Lite
- Reliable 5 V power supply
- Quality microSD card
- Ethernet cable from the Pi to the existing router
- A separate test Wi-Fi network hosted by the Pi
- Optional USB Ethernet adapter for a separate wired test segment

## Development setup

The target environment is Linux on the Raspberry Pi. Development can happen on macOS with the policy compiler and web service running in a simulated mode.

Planned prerequisites:

- Python 3.9 or newer
- Git
- A virtual environment
- `pytest` for tests
- Linux `tc`, `nft` and `ip` commands for hardware integration tests

The project will distinguish between two test modes:

1. **Simulation mode** runs on macOS or any development machine. It validates policies and records the commands that would have been executed.
2. **Hardware mode** runs on the Pi and applies rules to explicitly configured test interfaces.

The project must never assume that a developer's ordinary network interface is safe to modify.

### Safe local simulation

The current command is simulation-only. It prints the `nft` and `tc` operations that a later Pi deployment may execute:

```bash
PYTHONPATH=src python3 -m jittersim \
  --uplink eth0 \
  --test-interface wlan0 \
  --delay-ms 3000 \
  --jitter-ms 700 \
  --loss-percent 10 \
  --bandwidth-kbit 2000
```

Run the tests without installing packages globally:

```bash
PYTHONPATH=src python3 -B -m unittest discover -s tests -t .
```

The printed commands are not executed by the CLI. The subprocess adapter is a separate component and is not connected to the default command path.

## Testing strategy

The tests will focus on behavior that can be checked without a live network:

- Profile validation rejects unsafe or contradictory values.
- Device addresses are normalized and matched consistently.
- A policy compiles into the expected marking and traffic-control plan.
- State transitions are deterministic when a seed is supplied.
- Reapplying the same configuration is idempotent.
- A failed apply returns the system to the documented safe state.
- The emergency reset removes all project-owned rules.

Hardware checks will use a dedicated test client and commands such as `ping`, `curl` and `iperf3`. Traffic-control counters and measured latency will be recorded as evidence for the project report.

## Repository layout

The planned layout is:

```text
jittersim/
├── src/jittersim/
│   ├── api/              # Dashboard and HTTP API
│   ├── config/           # Profiles, devices and persistence
│   ├── engine/           # Policy compilation and reconciliation
│   ├── networking/       # tc, nftables and command adapters
│   └── schedules/        # Intermittent state transitions
├── tests/
│   ├── unit/
│   └── integration/
├── docs/
├── scripts/
├── README.md
├── ROADMAP.md
└── pyproject.toml
```

## Project constraints

The project should remain understandable to a student reviewing it during a presentation. Prefer small modules, explicit data structures and observable behavior over a large framework. Every feature should have a manual demonstration and a testable failure mode.

The initial project will not:

- Intercept HTTPS traffic.
- Read or modify application payloads.
- Control devices that bypass the Pi.
- Claim that packet loss equals request loss.
- Depend on a cloud service for the core experiment.
- Include credentials, private network exports or real customer data.

## References

- [Linux `tc-netem` manual](https://man7.org/linux/man-pages/man8/tc-netem.8.html)
- [Linux traffic-control netlink documentation](https://docs.kernel.org/networking/netlink_spec/tc.html)
- [nftables packet metadata and marks](https://wiki.nftables.org/wiki-nftables/index.php/Setting_packet_metainformation)
- [Raspberry Pi 3 Model B+ specifications](https://www.raspberrypi.com/products/raspberry-pi-3-model-b-plus/)

## License

The license will be selected before the first public release. Until then this repository is private and intended for the class project.
