# Household access point readiness

## Human review summary

The Pi can already give internet access to devices that have no JitterSim device
record. Only configured devices receive profile marks. That is useful for a
household network, but the current setup is an isolated test network. It blocks
the router's local network, uses IPv4 sharing and replaces the uplink's normal
traffic queue whenever a profile is applied. These choices need review before
the Pi becomes the household's main Wi-Fi.

Keep the isolated test setup as the default. Protect unconfigured devices with
a normal traffic queue and prove that profile changes do not disconnect them.
Add a separately selected household setup only after deciding whether clients
need access to wired printers, TVs and other local devices. A routed household
setup can allow local IPv4 access but still has NAT and does not provide normal
cross-network discovery or inbound connections. A bridged setup would need a
larger network redesign and another verification cycle. The built-in Pi radio's
coverage and client capacity need measurement in the actual house.

**Risk: high.** A faulty install, Wi-Fi setting, DHCP setting or traffic rule
could disconnect every household device. Keep the router's Wi-Fi available
during rollout and keep wired access to the Pi for recovery.

**Open questions:** Which Pi model and Raspberry Pi OS release will be used?
How many devices will join? Do Wi-Fi devices need normal access to equipment on
the router's wired network?

## Current behavior and gaps

1. NetworkManager owns the Wi-Fi AP, DHCP, DNS and IPv4 sharing. The helper and
   dashboard are separate services. A helper failure should therefore leave
   the AP and ordinary forwarding running, but this needs a failure test on the
   Pi.
2. A plan marks only configured device addresses. Unconfigured clients go
   through the root queue's default band when any device is shaped. Before
   this change, the band had no explicit fair queue, so household traffic
   could regress under load.
3. The management firewall blocks private upstream IPv4 and local IPv6 from
   Wi-Fi clients. This protects the test lab but prevents normal household
   access to wired devices.
4. IPv4 sharing creates a different subnet and NAT. Wired devices cannot
   generally initiate a connection back to Wi-Fi devices and local discovery
   protocols may not cross the boundary. IPv6 client service is disabled.
5. The AP has no measured coverage, client-count, sustained throughput or
   recovery evidence on the target Pi. A passing container test is not that
   evidence.
6. The installer requires a NetworkManager version with a configurable DHCP
   range. Before this change, it checked compatibility after creating the AP
   profile.

## Work that does not depend on the open questions

1. Give ordinary and unconfigured traffic a fair queue on both gateway
   interfaces. Preserve the existing limits on the number of shaped queues.
2. Test the default queue and a changing profile with real Linux `tc` and
   `nft`. Check reset and apply failure paths in a disposable container. Test
   an actual unconfigured client while the Pi AP is running before cutover.
3. Move installer compatibility and uplink checks before service changes. Make
   an incompatible Pi fail with a clear message while its existing Wi-Fi still
   works.
4. Add a repeatable Pi readiness and rollback checklist. Require a wired admin
   path, staged client migration, local-network tests and sustained load tests
   before disabling the router's Wi-Fi.

## Decision-dependent household setup

If local wired-network access is required, choose one of these explicitly:

- A routed mode can permit private upstream access. It keeps NAT and needs
  checks for printers, casting, games and inbound connections.
- A bridged access point can share the router's subnet, DHCP and discovery. It
  requires different packet classification and device addressing from the
  current routed gateway. Do not claim that the existing browser proxy or
  network plan works in a bridge without proving it on the Pi.

If internet-only use is sufficient, keep the existing isolation and explain
its limits in the setup guide.

## Verification and rollout

Run unit tests, lint, shell checks and the real Linux network smoke test on the
committed snapshot. Test an unconfigured client's DNS, web traffic and local
network access alongside one shaped client. On the Pi, leave the router Wi-Fi
on while at least two different client types join the JitterSim AP. Run a
sustained multi-device load check, restart the dashboard and helper, apply and
reset profiles and reboot the Pi. Measure reconnect time and verify ordinary
clients remain online. Test the browser proxy separately. Only then move the
remaining household devices and consider turning off router Wi-Fi. Restore the
router Wi-Fi and reset JitterSim if any check fails.

## Data and deployment review

The household setup reads client IPs, MAC addresses, traffic counters and the
configured hostname list. Keep the existing bounded local DNS observations and
avoid recording page content or credentials. The proxy remains opt-in for
managed browsers. Build and deploy from a clean checkout, retain the SSH reset
path and avoid an install step that cuts off the management Ethernet link.
