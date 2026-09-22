# JitterSim

JitterSim is a Raspberry Pi tool for testing apps on an unreliable internet
connection. The Pi connects to your Vodafone router by Ethernet then creates a
separate Wi-Fi network for test devices.

Use it only on equipment and networks that you own or are allowed to test. It
is a private lab tool, not a replacement for a home router.

## Features

### Profiles and network effects

- Delay, jitter, packet loss, bandwidth limits, packet reordering, duplication
  and corruption.
- Separate upload and download controls where the network supports them.
- Per-device profile assignment from the dashboard.
- A safe configuration-only mode for building plans without changing traffic.
- Hardware mode with a protected Pi helper that applies and resets plans.

### Schedules, timers and randomness

- Multiple daily time windows with click-to-select days and overnight ranges.
- One-shot timers that activate a profile for a chosen duration.
- Progression sessions that move from a starting profile to an ending profile
  over time, after a DNS-query target or after a device-data target, then
  recover automatically.
- Random value variation such as `200 ± 30%`.
- Random feature enablement, reroll timing and short connection outages.
- Inline help in the dashboard for the variation settings.

### Devices and website rules

- Connected-device discovery with device names, IP addresses and MAC addresses.
- Click-to-select devices and assign profiles from the dashboard.
- MAC address allowlists and denylists.
- A global blocked-site list.
- Profile-specific website lists that allow selected services while blocking
  the rest.
- Per-device website rules that can change as a progression session advances.
- Local DNS observations with query counts and buttons to add observed domains
  to a profile or the global block list.

### Data quotas

- Per-device byte quotas that switch to a fallback profile after the limit is
  reached.
- Per-service quotas that group several observed domains for one service such
  as a streaming platform.
- Manual, timed or daily quota resets.
- Usage bars, current usage, reset actions and editable limits in the dashboard.
- Quota changes that preserve the configured rule while resetting its counter.

### Pi and router controls

- The Pi's Ethernet network name and router gateway in the dashboard.
- Rename the Pi test access point and change its password locally.
- A direct link to the Vodafone router page.
- A visible status area for the current mode, connected devices, schedules,
  access rules and the last network action.

Traffic discovery stores local summaries of DNS names and query counts. Select
several observed names when creating a service quota because one service can
use many domains. A service quota counts matching IPv4 traffic as one service
group. It does not prove that someone opened a page.

Progression DNS mode uses DNS query counts as its signal. It does not count all
packets or measure download bytes. Device-data mode counts IPv4 upload and
download bytes for the selected device, so a long Netflix stream can advance a
progression even when it creates few new DNS queries.

## Network layout

```text
Vodafone router ── Ethernet ──> Raspberry Pi
                                  │
                                  ├── JitterSim test Wi-Fi
                                  └── JitterSim dashboard
```

Only devices on the JitterSim test Wi-Fi are affected. Devices that stay on the
router Wi-Fi bypass JitterSim. The current network rules use IPv4. IPv6 traffic
is not handled.

Test Wi-Fi clients can use the Pi for public internet access but cannot reach
the dashboard or private upstream addresses such as the Vodafone router's
`192.168.0.1`. Dashboard changes also require the administrator token.

Website rules, service quotas and device-data progression are best-effort. VPNs,
encrypted DNS, direct IP access, cached connections, changing service addresses
and IPv6 traffic can hide traffic or reduce the accuracy of a rule. The device
must use the Pi as its gateway for the rule to apply.

## Dashboard modes

- **Configuration only** saves settings and builds plans without changing live
  traffic.
- **Hardware mode** uses the Pi helper to apply the saved plan.

The Vodafone TG3442DE Wi-Fi switch is still a manual router action. JitterSim
can change its own test access point but Ethernet cannot read or copy the
router Wi-Fi name or password. Copy those values manually from the router
settings if you want the Pi access point to use them. See the
[Pi deployment guide](deploy/raspberry-pi/README.md).

## Run locally

Local mode is safe for development:

```bash
export JITTERSIM_ADMIN_TOKEN='use-a-long-local-token'
PYTHONPATH=src python3 -m jittersim.api --database ./jittersim.db
```

Open <http://127.0.0.1:8080/>. Run the tests with:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test*.py'
```

## Project layout

- `src/jittersim/` — dashboard, profiles, schedules and network helper
- `deploy/raspberry-pi/` — Pi services and installation files
- `tests/` — automated checks
- [`ROADMAP.md`](ROADMAP.md) — future work and known limits
