# jittersim implementation plan

Status: historical. This is the first plan, written in September 2026. It stays
as the record of the original design and is not kept up to date. The three
decisions it lists below as open are settled in the code: the dashboard uses
Python's standard `http.server` (`src/jittersim/api/server.py`), the privileged
helper is a Unix-socket service (`src/jittersim/networking/helper.py`) and
private data and controls need the admin token plus, on the test Wi-Fi, saved
device access. For what the product does today read the
[README](README.md) and the [Pi guide](deploy/raspberry-pi/README.md). The
[documentation index](docs/README.md) lists the later plans.

## Summary for review

`jittersim` will be a Raspberry Pi network-emulation appliance for an isolated test network. A browser dashboard will manage device profiles while a Linux traffic-control engine applies the profiles. The design separates the web control plane from the privileged networking data plane so policy validation can be tested on a Mac without modifying its network.

The first useful release will support IPv4 devices identified through DHCP reservations. It will apply delay, jitter, packet loss and rate limits in both directions. Intermittent behavior will be represented by a small state machine that changes between normal, degraded and offline states. The main risks are accidentally modifying the wrong interface, losing access to the dashboard and confusing packet loss with request-level failure. Interface allowlists, an emergency reset and simulation mode are required before hardware integration.

Open decisions are the exact Python web framework, the authentication method for the private dashboard and whether the final helper uses a Unix-domain socket or a narrowly configured system service. These decisions do not block the policy model or simulation work.

## Learning objective

The central learning objective is translating a high-level network policy into safe low-level operations. The project should make the boundary visible:

```text
profile → validated policy → marked traffic classes → tc/nft operations → measured behavior
```

The web interface and Raspberry Pi setup are supporting work. The main reasoning belongs in policy validation, direction handling, state transitions and safe recovery.

## Scope

### In scope

- A private web dashboard controlled from a Mac.
- A Raspberry Pi gateway for an isolated test network.
- Per-device IPv4 policies.
- Delay and jitter.
- Packet loss.
- Upload and download bandwidth limits.
- Intermittent normal, degraded and offline states.
- Configuration persistence in SQLite.
- Simulation mode for non-Linux development.
- Logs, counters and reset behavior.

### Out of scope for the first release

- HTTPS interception.
- Application payload modification.
- Exact HTTP request loss percentages.
- Automatic changes to an existing home router.
- Public access to the dashboard.
- IPv6 impairment before IPv6 policy handling is implemented.

## Design decisions

### Gateway topology

The Pi will host or route a separate test network. Controlled devices must use the Pi as their default gateway. This is more predictable for a class demonstration than attempting to interfere with devices that remain on an unrelated access point.

### Device identity

DHCP reservations provide stable IPv4 addresses. The dashboard may display a MAC address as a hint, but policy enforcement will use the reserved address. The design will explicitly document the effect of randomized client MAC addresses.

### Traffic classification

The compiler will create project-owned nftables marks for source and destination addresses. Traffic-control classes will use those marks. Both directions must be planned because shaping only outbound traffic would produce an incomplete experiment.

### Intermittent behavior

The scheduler will operate on states rather than rebuilding rules on every packet. State transitions will be the only place that changes the active profile. A seed makes a scenario repeatable for tests and a class demonstration.

### Privilege boundary

The dashboard should not need root privileges. The networking adapter will eventually run behind a narrow privileged helper. The helper will receive structured operations, validate interface names and execute only the supported networking actions.

### Failure behavior

The safest default after a process failure is to remove project-owned impairment rules. A reset operation must be available through both the dashboard and SSH. Applying the same desired configuration twice must not create duplicate classes or rules.

## Work sequence

1. Document the scope and safety boundary.
2. Build the package scaffold and simulation mode.
3. Implement policy models and validation.
4. Implement deterministic plan compilation.
5. Implement fake apply, reset and failure recovery.
6. Add real command execution behind explicit hardware mode.
7. Add SQLite persistence.
8. Add the API and dashboard.
9. Add schedules and seeded intermittent behavior.
10. Deploy to the Pi and test with a dedicated client.
11. Add counters and class-demonstration measurements.

## Verification strategy

### Local verification

- Run unit tests against the fake executor.
- Check that invalid values are rejected before command generation.
- Compare generated plans with golden text fixtures.
- Apply the same plan twice and verify that the second plan is a no-op or safe replacement.
- Simulate command failure and verify cleanup.
- Run the dashboard with all networking operations in dry-run mode.

### Hardware verification

- Confirm the management device can always reach the dashboard.
- Confirm an unassigned test device remains normal.
- Confirm one assigned test device shows the expected delay.
- Measure loss over enough packets to distinguish random behavior from a single failure.
- Measure bandwidth in both directions.
- Trigger an outage and confirm the device recovers after the state ends.
- Restart the service and confirm the documented recovery behavior.
- Run the reset path after every destructive experiment.

### Security and privacy checks

- The dashboard listens only on the private management network.
- State-changing endpoints require authentication.
- No credentials or private network captures are committed.
- Subprocess calls do not use a shell.
- Interface names come from an allowlist.
- Logs contain counters and event metadata rather than packet payloads.
- The project has no production or public-network deployment path.

## Rollback and recovery

The project will keep all traffic-control handles and nftables table names under a project-owned namespace. Reset removes only those objects. A systemd stop hook and an SSH command will call the same reset operation. If the helper cannot verify the configured test interfaces, it will refuse to apply a policy.

## Review checkpoints

The following checkpoints should be reviewed before moving to the next layer:

- Policy values and units are clear.
- Direction handling is explicit.
- Generated operations are bounded and reversible.
- The failure path is tested before hardware access.
- The management path is exempt from impairment.
- The class demonstration can be repeated from a saved scenario.

