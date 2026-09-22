# JitterSim

JitterSim is a Raspberry Pi tool for testing apps on an unreliable internet
connection. The Pi connects to your Vodafone router by Ethernet then creates a
separate Wi-Fi network for test devices.

Use it only on equipment and networks that you own or are allowed to test. It
is a private lab tool, not a replacement for a home router.

## What works

- Profiles for delay, jitter, packet loss, speed limits and packet changes.
- Per-device profile assignment from the dashboard.
- Multiple daily schedules with overnight time ranges.
- One-shot profile timers that temporarily override a device then end automatically.
- Progression sessions that move from one profile to another over time or after
  a DNS-query target, then recover automatically.
- Click-to-select schedule days with inline help for random settings.
- Seeded randomness for values, feature enablement, rerolls and short outages.
- MAC address allowlists and denylists.
- Global blocked websites and profile-specific allow-only website lists.
- Per-device website blocking that can grow as a progression session advances.
- Connected-device discovery and short DNS traffic summaries.
- Buttons that add observed domains to a profile or the global block list.
- Device data limits that switch to a fallback profile after a byte quota is reached.
- Service data limits that group several observed DNS domains such as a streaming service.
- Manual, timed or daily quota resets without losing the configured limit.
- A protected helper service with Apply and Reset controls.
- Local Pi test Wi-Fi name and password changes from the dashboard.
- The Pi's Ethernet connection name and router gateway in the dashboard.

The dashboard shows the current mode, connected devices, schedules, access
rules and the last network action near the top of the page.

Traffic discovery stores local summaries of DNS names and query counts. Select
several observed names when creating a service quota because one service can
use many domains. The quota counts matching IPv4 traffic as one service group.
It does not prove that someone opened a page. A VPN, encrypted DNS or direct IP
access can hide or bypass these observations and website rules.

Progression traffic mode uses DNS query counts as its signal. It does not count
all packets or measure download bytes.

## Network layout

```text
Vodafone router ── Ethernet ──> Raspberry Pi
                                  │
                                  ├── JitterSim test Wi-Fi
                                  └── JitterSim dashboard
```

Only devices on the JitterSim test Wi-Fi are affected. Devices that stay on the
router Wi-Fi bypass JitterSim. The first version uses IPv4. IPv6 traffic is not
handled.

Test Wi-Fi clients can use the Pi for public internet access but cannot reach
the dashboard or private upstream addresses such as the Vodafone router's
`192.168.0.1`. Dashboard changes also require the administrator token.

## Dashboard modes

- **Configuration only** saves settings and builds plans without changing live
  traffic.
- **Hardware mode** uses the Pi helper to apply the saved plan.

The Vodafone TG3442DE Wi-Fi switch is still a manual router action. JitterSim
can change its own test access point but Ethernet cannot provide the router
Wi-Fi name or password. Copy those values manually from the router settings.
See the [Pi deployment guide](deploy/raspberry-pi/README.md).

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
