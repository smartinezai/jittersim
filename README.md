# JitterSim

JitterSim is a Raspberry Pi tool for testing how apps behave on an unreliable
internet connection. The Pi connects to your normal router by Ethernet. It
creates a separate test Wi-Fi network where you can try slower speeds, delays,
packet loss and short outages.

Use JitterSim only on equipment and networks that you own or are allowed to
test. It is a private lab tool, not a replacement for a home router.

## What works now

- A web dashboard for saving profiles and assigning them to devices.
- Profiles with delay, delay variation, packet loss and speed limits.
- A list of devices currently connected to the test Wi-Fi network.
- One-click profile assignment for a connected device.
- Apply and reset buttons with a clear status message.
- A separate protected service that performs network changes with limited commands.
- A traffic discovery scan that shows internet names such as `amazon.com`
  requested by each test device.

Traffic discovery is a review tool. It stores short local summaries of these
names and how often they were requested. It does not prove that a person
opened a page. Apps often contact services in the background. Privacy tools
such as encrypted name lookups or a VPN can hide the names.

## Network layout

```text
Normal router ── Ethernet ──> Raspberry Pi
                              │
                              ├── JitterSim test Wi-Fi
                              └── JitterSim dashboard
```

Only devices connected through the Pi are affected. A device that stays on the
normal router Wi-Fi bypasses JitterSim.

The current setup uses the common IPv4 address format and an isolated test
network. IPv6 traffic is not handled by the first version.

## Dashboard

The dashboard lets you:

1. Create a profile such as `bad-wifi`.
2. Set the delay, delay variation, packet loss and speed limits.
3. Assign the profile to a saved device or a device shown as connected.
4. Apply the configuration.
5. Use Reset network to remove JitterSim's network rules.

The dashboard runs in two modes:

- **Configuration mode** saves settings but does not change network traffic.
- **Hardware mode** uses the Raspberry Pi helper to apply the settings.

## Run locally

Local mode is safe for development. It stores settings and builds the network
plan without changing the computer's own network.

```bash
export JITTERSIM_ADMIN_TOKEN='use-a-long-local-token'
PYTHONPATH=src python3 -m jittersim.api --database ./jittersim.db
```

Open <http://127.0.0.1:8080/> in a browser.

Run the tests with:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test*.py'
```

## Raspberry Pi setup

The Pi installer and its service files are in
[`deploy/raspberry-pi/`](deploy/raspberry-pi/README.md). The installer creates
the local credentials on the Pi and installs the dashboard, helper, test
Wi-Fi and internet-sharing services.

The Pi should use Ethernet for its connection to the normal router. The test
devices should connect to the JitterSim Wi-Fi network instead.

## Not implemented yet

These ideas are planned but are not part of the current release:

- schedules with several active time ranges;
- random changes to delay, loss, speed and outage timing;
- website and service allowlists or denylists;
- device hardware-address (MAC address) allowlists and denylists;
- automatic suggestions for services based on longer traffic history;
- automatic control of the Vodafone TG3442DE router's Wi-Fi.

The router's Wi-Fi can be disabled manually while the Pi remains connected by
Ethernet. JitterSim does not copy or retrieve the router's password.

## Project layout

- `src/jittersim/` — dashboard, profiles, plan building and network helper
- `deploy/raspberry-pi/` — Pi services and installation files
- `tests/` — unit and integration tests
- [`ROADMAP.md`](ROADMAP.md) — longer-term plans
