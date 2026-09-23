# JitterSim

JitterSim is a Raspberry Pi tool for testing apps on an unreliable internet
connection. The Pi connects to your Vodafone router (because that's what I have so no idea if it works with other routers) by Ethernet then creates a
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
- Device groups that apply one profile override to several devices at once.
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
- Shared group byte quotas that add the traffic from all group members. A
  per-device quota takes priority when both are configured.
- Per-service quotas that group several observed domains for one service such
  as a streaming platform.
- Service quota weights from `0.1×` to `10×`, so a service can consume its
  quota faster or slower than its actual byte count.
- Service time quotas that allow active traffic for a set number of minutes or
  hours inside a timer window such as 5 hours in a 12-hour session.
- Time quotas can start, stop and reset from the dashboard and can reset after
  an interval or at several times each day.
- Manual, timed or daily quota resets.
- Usage bars, current usage, reset actions and editable limits in the dashboard.
- Quota changes that preserve the configured rule while resetting its counter.

Groups are limited to one group per device. Service and timed service quotas
remain device-specific, while shared group quotas measure combined device
bytes.

### Pi and router controls

- The Pi's Ethernet network name and router gateway in the dashboard.
- Rename the Pi test access point and change its password locally.
- Enter the Vodafone TG3442DE admin password for one action at a time.
- Copy the router's primary Wi-Fi name and password to the Pi access point.
- Turn the router's primary Wi-Fi on or off from the dashboard.
- A direct link to the Vodafone router page for settings JitterSim cannot
  change.
- A visible status area for the current mode, connected devices, schedules,
  access rules, diagnostics and a short history of admin actions.
- Download a point-in-time backup of settings and local DNS observations.
- Restore the last applied or reset state when the dashboard service restarts.

Traffic discovery stores local summaries of DNS names and query counts. Select
several observed names when creating a service quota because one service can
use many domains. Resolved addresses refresh at most once a minute. A service
quota counts matching IPv4 traffic as one service group then applies its
weight. For example, `1.2×` charges 120 MB of quota usage for 100 MB of
matching traffic while `0.8×` charges 80 MB. It does not prove that someone
opened a page.

Time quotas count active matching traffic between counter samples. A long
Netflix stream can therefore use the time allowance even when there are no
new clicks. They are an observed-traffic estimate, not a media player timer,
and only work while the device uses the Pi as its gateway.

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
router Wi-Fi bypass JitterSim. Device rules and quotas currently match IPv4.
While a network plan is applied, IPv6 forwarding from the test Wi-Fi is blocked
so it cannot bypass those rules. Resetting the network removes that block.

Test Wi-Fi clients can use the Pi for public internet access but cannot reach
the dashboard or private upstream addresses such as the Vodafone router's
`192.168.0.1`. Dashboard changes also require the administrator token.

Website rules, service quotas and device-data progression are best-effort. VPNs,
encrypted DNS, direct IP access, cached connections and shared hosting can hide
traffic or reduce accuracy. The device must use the Pi as its gateway. Backups
include device details and observed domain names, so keep them private.

## Dashboard modes

- **Configuration only** saves settings and builds plans without changing live
  traffic.
- **Hardware mode** uses the Pi helper to apply the saved plan.

Enter the admin token in the **Control token** box and click **Check token** to
load diagnostics. Dashboard actions show when they start and whether they
worked, without requiring you to scroll to the status area.

For a Vodafone TG3442DE, the dashboard can read the primary Wi-Fi settings and
turn the primary Wi-Fi radios on or off. The router password is used for one
authenticated request and is not saved. Keep the Pi connected by Ethernet
before changing router Wi-Fi. If a router firmware update changes its local
web interface, use the direct router link or the manual Pi access-point form.
See the [Pi deployment guide](deploy/raspberry-pi/README.md).

On the Pi, open the dashboard over HTTPS at `https://jittersim.local:8080` or
the Pi's Ethernet address. The installer creates a local certificate and prints
its SHA-256 fingerprint. Verify that fingerprint before trusting the
certificate in your browser. A DHCP address change may require a new
certificate. HTTPS protects the browser-to-Pi connection, but the TG3442DE's
own local web interface still uses HTTP between the Pi and router.

## Run locally

Local mode is safe for development:

```bash
export JITTERSIM_ADMIN_TOKEN='use-a-long-local-token'
PYTHONPATH=src python3 -m jittersim.api --database ./jittersim.db
```

Open <http://127.0.0.1:8080/>. Local development uses loopback HTTP; the Pi
service uses HTTPS. Run the tests with:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test*.py'
```

## Project layout

- `src/jittersim/` — dashboard, profiles, schedules and network helper
- `deploy/raspberry-pi/` — Pi services and installation files
- `tests/` — automated checks
- [`ROADMAP.md`](ROADMAP.md) — future work and known limits
