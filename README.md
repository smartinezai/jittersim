# JitterSim

JitterSim is a Raspberry Pi tool for testing apps on an unreliable internet
connection. The Pi connects to a router by Ethernet then creates a separate
Wi-Fi network for test devices. The optional router controls currently support
the Vodafone TG3442DE.

Use it only on equipment and networks that you own or are allowed to test. It
is a private lab tool, not a replacement for a home router.

## Features

### Profiles and network effects

- Delay, jitter, packet loss, bandwidth limits, packet reordering, duplication
  and corruption.
- Loss that arrives in bursts of several packets, with the configured
  percentage as the average.
- Jitter that keeps packets in order. The Reorder setting reorders on purpose.
- Presets that fill the profile form with rough approximations of common
  networks, such as 3G, satellite and a train tunnel with outages.
- Separate upload and download controls where the network supports them.
- Per-device profile assignment from the dashboard.
- A safe configuration-only mode for building plans without changing traffic.
- Hardware mode with a protected Pi helper that applies and resets plans.
- Browser service profiles that shape selected HTTPS hostnames on a managed Mac
  browser while other proxied sites keep the device's ordinary profile.

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

- Connected-device discovery with current Wi-Fi connection status, IP and MAC
  addresses, signal strength and DHCP names when available.
- Device type suggestions from local DHCP names plus editable names and types
  stored on the Pi. Categories are phone, tablet, laptop, desktop, TV or
  console, other and unknown. Type labels never change traffic rules.
- Click-to-select devices and assign profiles from the dashboard.
- Tables of saved profiles and devices with Edit and Delete, plus Delete for
  schedules and limits. A profile that is still in use cannot be deleted.
- Device groups that apply one profile override to several devices at once.
- One profile for everyone else on the test Wi-Fi, with an Unaffected devices
  list whose members keep a normal connection.
- A live upload and download graph with packet rates for assigned devices or
  everyone else, and the packets JitterSim dropped or is still delaying.
- Saved devices follow their MAC address: when the Wi-Fi gives one a new IP
  address, JitterSim moves its record within about 30 seconds of seeing the
  device active there, while the network is applied. Devices that swap
  addresses move together, and no device takes an address another saved
  device keeps.
- MAC address allowlists and denylists. The filtering mode decides which list
  applies.
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
- Export settings as JSON and import them on another Pi or to repeat a
  demonstration. Imports are checked first and replace all settings at once.
- Restore the last applied or reset state when the dashboard service restarts.
- Clear every network change over SSH with `sudo jittersim reset`, even when
  the dashboard is down.
- Optional private remote dashboard access through Tailscale Serve.

### Browser service profiles

Save a **Browser service profile** with a test device, a profile and one or
more HTTPS hostnames. For example, one assignment can list `linkedin.com`,
`netflix.com`, `google.com`, `microsoft.com` and `youtube.com`. A hostname also
matches its subdomains. Separate assignments can give each service a different
profile. These names are starting points. Add other hostnames actually used by
each service, such as media or sign-in hosts, when you need them covered.

On a Mac connected to the JitterSim test Wi-Fi, set the browser's **HTTPS
proxy** to `192.168.50.1` port `3128`. The proxy uses the requested hostname
for each HTTPS tunnel. Two sites on the same server address can receive
different profiles. Other HTTPS tunnels use that device's ordinary profile.
Traffic from devices outside the test Wi-Fi stays outside JitterSim. Existing
proxy tunnels close when a new network plan is applied so the browser can open
them with the current profile.

This strict hostname selection covers HTTPS connections that the managed
browser sends through the proxy. Native apps, browser traffic that bypasses the
proxy, VPN traffic, plain HTTP, QUIC and connections without a hostname are not
classified by the browser service profile. They can still receive the ordinary
device profile. The test Wi-Fi is IPv4 only. A service may need several
hostnames, so verify the actual requests for your test case. Device traffic
counters include browser proxy tunnels. Existing service byte and time quotas
use observed DNS and address matching for direct traffic and do not count
browser proxy tunnels. See the [Pi guide](deploy/raspberry-pi/README.md)
for Mac setup and a repeatable isolation check.

One applied plan supports at most 15 combined devices and service rules and
200 browser service hostnames. The dashboard rejects overlapping service
domains on the same device and rejects server IP addresses as service names.

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

### Measurements

- Each device shows whether it is normal, degraded or offline, and why.
- `jittersim measure` on a test device pings a public host and saves the
  loss, round trips, jitter, reordering and, optionally, a download speed.
  `jittersim compare` turns saved runs into a Markdown or CSV table.
- The dashboard's **Measurements** panel compares saved runs, draws their
  round trips and copies the table for a report.

See [Measured effect of the profile presets](docs/measurements-2026-09.md)
for tables measured through JitterSim's real queues, and how to repeat the
measurements on the Pi.

## Network layout

```text
Vodafone router ── Ethernet ──> Raspberry Pi
                                  │
                                  ├── JitterSim test Wi-Fi
                                  └── JitterSim dashboard
```

Only devices on the JitterSim test Wi-Fi are affected. Devices that stay on the
router Wi-Fi bypass JitterSim. Devices on the test Wi-Fi can't reach each
other: the access point isolates its clients, so a visitor can't see your
laptop's shared folders, and casting between test Wi-Fi devices doesn't work.
The [Pi guide](deploy/raspberry-pi/README.md#client-isolation) shows how to
turn that off. The test Wi-Fi uses IPv4 sharing. Device rules
and quotas match IPv4, while the browser proxy can connect to public IPv4 or
IPv6 hosts for managed HTTPS traffic.

Any household device can join the JitterSim Wi-Fi without a JitterSim device
record. The DHCP service and ordinary internet forwarding run in
NetworkManager, independently of the dashboard and network helper. A network
plan marks devices you add in the dashboard. Other clients get a normal
connection unless you choose a profile for everyone else. Everyone else then
shares one queue with that profile, so its speed limit is shared by all of
them. Devices on the Unaffected devices list keep a normal connection. The
list matches MAC addresses, so use the address shown in Connected clients, and
keep a phone's private Wi-Fi address fixed for the JitterSim network.

The connected-device list shows devices currently associated with the Pi's
Wi-Fi. It can show the IP address, signal strength and a DHCP-provided network
name. Type suggestions use only clear words in that name and may be wrong.
Names and types you save stay on the Pi and do not affect traffic. They are
saved against the Wi-Fi MAC address. Apple and Android devices can use private
addresses that change or differ by network, so a saved label may need to be
entered again after an address changes. Offline labels can be removed from the
dashboard. Up to 256 labels are saved. See [Apple's private Wi-Fi address
guide](https://support.apple.com/en-ie/102509) and [Android's MAC
randomization guide](https://source.android.com/docs/core/connect/wifi-mac-randomization-behavior).

**Do not switch the whole house to this AP yet.** The current setup is a
separate IPv4 network behind NAT. Its firewall intentionally blocks access to
the router's private network, which can break wired printers, TVs, NAS and
other local services. It does not offer client IPv6 or normal discovery across
the router boundary. The Pi's Wi-Fi coverage, sustained throughput and client
capacity have not been measured in your house. A Pi outage would disconnect
every device using its Wi-Fi. Keep the router's Wi-Fi available while following
the [household trial](deploy/raspberry-pi/README.md#household-wi-fi-trial).

The target Pi 3 Model B+ has 1 GB of memory and a 300 Mbps maximum Ethernet
throughput. Its built-in Wi-Fi supports 2.4 GHz and 5 GHz. These specifications
do not establish the speed, coverage or client count the AP will achieve in
your home. Use a good 5 V/2.5 A power supply and set the WLAN country before
the trial. See the [official hardware specifications](https://www.raspberrypi.com/products/raspberry-pi-3-model-b-plus/)
and [wireless setup guide](https://www.raspberrypi.com/documentation/computers/configuration.html).

Test Wi-Fi clients can use the Pi for public internet access but cannot reach
the dashboard or private upstream addresses such as the Vodafone router's
`192.168.0.1`. Dashboard changes also require the administrator token.

Website rules, service quotas and device-data progression are best-effort. VPNs,
encrypted DNS, direct IP access, cached connections and shared hosting can hide
traffic or reduce accuracy. The device must use the Pi as its gateway. Backups
include device details and observed domain names, so keep them private.

The live graph samples the Pi's device counters every two seconds and shows the
latest minute. Solid lines show traffic seen by JitterSim before its queues,
not the speed a device successfully receives. The dropped and waiting figures
come from JitterSim's own queues. Dashed lines show manually configured ISP
plan limits and the selected device's applied default-queue bandwidth limits.
The initial ISP download reference is 100 Mbit/s and upload is unset. Edit
these values to match the connection. They are graph references only and do
not change network traffic.

The ISP plan limit is shared with other home devices and is not a live measure
of spare bandwidth for JitterSim. Select one device to see its default-queue
limits. Service-specific queues can use different limits, so those profile
lines do not represent every flow from that device. Run a speed test on the
connected device to check its experienced speed.

## Dashboard modes

- **Configuration only** saves settings and builds plans without changing live
  traffic.
- **Hardware mode** uses the Pi helper to apply the saved plan.

Sign in with the admin token. On the Pi, `sudo grep JITTERSIM_ADMIN_TOKEN
/etc/jittersim/control.env` shows it. The browser stays signed in for 7 days or
until you choose **Sign out**. Anyone without the token sees only a plain
sign-in page that doesn't name JitterSim or its features, and an address that
enters a wrong token 5 times waits 15 minutes. Scripts can still send the token
as `Authorization: Bearer <token>`. Dashboard actions show when they start and
whether they worked, without requiring you to scroll to the status area.

The dashboard's sections sit in tabs: Overview, Devices, Profiles, Rules &
limits, Measurements, Setup and Diagnostics. The address names the open tab,
so a reload or bookmark returns to it. A **Getting started** checklist on the
Overview guides a first setup and disappears once it is done. Forms that need a
device's address offer a menu of the connected devices, with **Refresh**, so
addresses don't have to be typed. The page fits a phone screen.

For a Vodafone TG3442DE, the dashboard can read the primary Wi-Fi settings and
turn the primary Wi-Fi radios on or off. The router password is used for one
authenticated request and is not saved. Keep the Pi connected by Ethernet
before changing router Wi-Fi. If a router firmware update changes its local
web interface, use the direct router link or the manual Pi access-point form.
See the [Pi deployment guide](deploy/raspberry-pi/README.md).

For control from another location, join the Pi and laptop to your Tailscale
network and privately serve the dashboard over the Pi's Tailscale name. Keep
the admin token. This does not route the laptop's general internet traffic
through the Pi. The [Pi guide](deploy/raspberry-pi/README.md#remote-control-with-tailscale)
has setup, access rules, checks and removal steps. Tailscale's Personal plan
is [free for eligible home use](https://tailscale.com/pricing).

On the Pi, open the dashboard over HTTPS at `https://<pi-name>.local:8080`,
where `<pi-name>` is the Pi's host name, or at its Ethernet address. The
installer creates a local certificate named after the Pi and prints its SHA-256
fingerprint. If the Pi's name gives JitterSim away, rename the Pi and run the
installer again. Verify that fingerprint before trusting the
certificate in your browser. A DHCP address change may require a new
certificate. HTTPS protects the browser-to-Pi connection, but the TG3442DE's
own local web interface still uses HTTP between the Pi and router.

## Run locally

Local mode is safe for development:

```bash
python3 -m venv .venv
.venv/bin/pip install '.[dev]'
export JITTERSIM_ADMIN_TOKEN='use-a-long-local-token'
PYTHONPATH=src .venv/bin/python -m jittersim.api --database ./jittersim.db
```

Open <http://127.0.0.1:8080/>. Local development uses loopback HTTP; the Pi
service uses HTTPS. Run the tests with:

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p 'test*.py'
```

To check real Linux `tc`, `nft` and socket marking inside a disposable Docker
network namespace:

```bash
docker run --rm --cap-add NET_ADMIN --cap-add NET_RAW \
  -v "$PWD":/code:ro -w /code debian:bookworm-slim sh -c \
  'apt-get update -qq && apt-get install -y -qq iproute2 nftables python3 >/dev/null && PYTHONPATH=src python3 tests/integration/linux_network_smoke.py'
```

The tests in `tests/kernel` apply real `tc` and `nftables` commands inside
network namespaces. They are skipped unless you run them as root on Linux with
`JITTERSIM_KERNEL_TESTS=1`. On a Mac, run them in a privileged container. The
same run ends with the smoke check above:

```bash
tests/kernel/run-in-docker.sh
```

GitHub Actions runs the unit tests on Python 3.9, 3.11 and 3.13, a lint check
for errors that break code, and these Linux checks on every pull request.

## Project layout

- `src/jittersim/` — dashboard, profiles, schedules and network helper
- `deploy/raspberry-pi/` — Pi services and installation files
- `tests/` — automated checks
- [`ROADMAP.md`](ROADMAP.md) — future work and known limits
