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
- Separate upload and download controls where the network supports them.
- Per-device profile assignment from the dashboard.
- A safe configuration-only mode for building plans without changing traffic.
- Hardware mode with a protected Pi helper that applies and resets plans.
- Browser service profiles that shape selected HTTPS hostnames on a managed Mac
  browser while other proxied sites keep the device's ordinary profile.
- Service lists that give chosen apps and sites their own profile on every
  affected device with no per-device setup.

### Schedules, timers and randomness

- Multiple daily time windows with click-to-select days and overnight ranges.
- One-shot timers that activate a profile for a chosen duration.
- Progression sessions that move from a starting profile to an ending profile
  over time, after a DNS-query target or after a device-data target, then
  recover automatically.
- Random value variation such as `200 ± 30%`.
- Random feature enablement, reroll timing and short connection outages.
- Inline help in the dashboard for the variation settings.
- Daily counts of kept and removed website choices for scheduled random
  profile rerolls, with up to 30 days stored on the Pi. These counts show the
  generated profile choices, not site visits or confirmed network blocks.

### Devices and website rules

- Connected-device discovery with current Wi-Fi connection status, IP and MAC
  addresses, signal strength and DHCP names when available.
- Device type suggestions from local DHCP names plus editable names and types
  stored on the Pi. Categories are phone, tablet, laptop, desktop, TV or
  console, other and unknown. Type labels never change traffic rules.
- Click-to-select devices and assign profiles from the dashboard.
- Device groups that apply one profile override to several devices at once.
- Add connected clients to groups without entering their IP or MAC address.
- A saved list of devices unaffected by all profiles and profile quotas.
- A live upload and download graph with packet rates for assigned devices.
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

The left sidebar, visible on every page, includes one **Router Wi-Fi** switch.
It shows the router's confirmed On or Off state and the last check time.
Enter the router admin password to start automatic checks while the page is
visible. A change shows **Changing** until the router confirms it. An
unavailable status disables the switch instead of guessing. Keep the Pi
connected by Ethernet when changing router Wi-Fi.

**Copy router Wi-Fi to Pi** checks the router's security before copying its
name and password. The confirmation shows the source band and security type.
JitterSim copies personal WPA, WPA2, WPA3 and mixed modes when the Pi can
verify that mode. Older WPA needs a weak-security warning and confirmation.
Unknown modes, open networks, WEP and enterprise Wi-Fi are rejected. An
unsupported Pi mode restores the previous Wi-Fi settings instead of choosing
another mode. The router itself is not changed by copying.

A device asking for a WPA, WPA2 or WPA3 password needs the Wi-Fi password for
that network. After copying router Wi-Fi this is the router's Wi-Fi password.
It is different from the router administration password and dashboard token.

Use **Reset Wi-Fi name** on the **Router and Wi-Fi** page or run
`sudo jittersim wifi reset` on the Pi to restore `JitterSim`. Both keep the
current password and security type. The action restarts Wi-Fi and disconnects
test clients. Add `--yes` to confirm without an interactive prompt. The console
works with the dashboard stopped and needs the local helper.

Fresh installations and new manual Wi-Fi settings use WPA3 with AES.
Upgrades preserve the existing security type. Runtime changes inspect the
active radio before reporting success. Mixed-mode support depends on the
Pi's software and Wi-Fi radio. Saved settings alone do not prove support.

Copying the router's Wi-Fi name can make the debugging computer join the Pi.
Use saved dashboard access below to keep control while testing. SSH still
requires the router network or a local Pi terminal.

### Dashboard access from Pi Wi-Fi

Open the HTTPS dashboard using the Pi address covered by its certificate,
for example `https://192.168.0.17:8080`. Choose **Admin token** then **Check
token** and **Allow this device**. This registers the actual requesting
computer. The dashboard lists saved devices and supports removal.

Only dashboard traffic avoids simulated loss and client blocks. Your ordinary
internet traffic still follows its profile. The separate unaffected-device
list continues to cover broader profile exemptions. SSH and access to private
router devices remain restricted.

The limited sign-in page is reachable before registration. Private data,
downloads and controls require the token plus saved access on Pi Wi-Fi.
The hardware address is not a password. Anyone holding the admin token may
register their own device. Changed private Wi-Fi addresses need registration
again. If current device observations cannot be verified, reconnect and retry.

From your **Mac**, with JitterSim installed and a trusted copy of the Pi's
public dashboard certificate:

```sh
jittersim dashboard-access allow-self --url https://192.168.0.17:8080 \
  --ca-cert ~/jittersim-dashboard.crt --name 'My Mac'
```

The hidden prompt asks for the **JitterSim admin token**. Certificate checks
stay enabled. No `sudo` or Linux firewall command is needed on the Mac. Copy
`/etc/jittersim/dashboard.crt` through a previously trusted Pi connection.
Do not copy the private key or accept an unverified replacement certificate.

From a **Pi terminal**, root recovery works while the dashboard is stopped:

```sh
sudo jittersim dashboard-access add 02:00:00:00:00:01 --name 'My Mac'
sudo jittersim dashboard-access list
sudo jittersim dashboard-access remove 02:00:00:00:00:01
sudo jittersim dashboard-access status
```

Use your device's current Wi-Fi hardware address. Here `sudo` asks for the
Pi account password. The helper must be running. Entries survive restart,
reboot and upgrades in root-owned `/etc/jittersim/dashboard-access.json`.
Include this file in private Pi backups. Dashboard SQLite downloads do not
contain it. The file contains device addresses, optional names and addition
times. It contains no tokens or traffic history and removal deletes the entry.
Saved access remains usable while the Pi refreshes a connected device's
address. Adding access requires a freshly confirmed device address.

An initial upgrade of an older release still needs wireless access through
the real router or a local Pi terminal because its firewall predates sign-in.
Rollback restores that older access behavior while retaining the saved file.

- The Pi's Ethernet network name and router gateway in the dashboard.
- Rename the Pi test access point and change its password locally.
- Enter the Vodafone TG3442DE admin password for one action at a time.
- Check and copy the router's Wi-Fi name, password and security to the Pi.
- Turn the router's primary Wi-Fi on or off from the dashboard.
- A direct link to the Vodafone router page for settings JitterSim cannot
  change.
- A visible status area for the current mode, connected devices, schedules,
  access rules, diagnostics and a short history of admin actions.
- Download a point-in-time backup of settings and local DNS observations.
  Random website-choice analytics stay on the Pi and are left out of downloads.
- Restore the last applied or reset state when the dashboard service restarts.
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
affected proxy tunnels close when a new network plan is applied so the browser
can open them with the current profile. Protected clients keep their tunnels
when another client's profile changes. Removing protection or changing global
access rules can close those tunnels.

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

### Service lists

A **Service list** applies a profile to chosen apps and sites on every affected
device. Find it under **Schedules and limits**. Tick services from the catalogue (LinkedIn, Microsoft Teams, ChatGPT,
Claude, Netflix, YouTube and more) and add any extra hostnames then choose the
profile for them. You can keep several lists with a different profile each.

While a list is enabled, traffic from an affected device to its services gets
that profile instead of whatever the device would otherwise get. That
includes no profile at all, a device, group, schedule, timer, progression or
quota profile, a browser service profile and the general profile. A device with
no profile browses normally until it opens one of the listed services. All
other traffic is untouched. The list applies to upload and download traffic of
any protocol including QUIC. Devices in **Unaffected by profiles** are never
touched. Global MAC and website rules still apply. A random outage or website
list from the device's own profile does not cut a listed service. A device in
a random outage still loses the Pi's DNS and browser proxy so it reaches a
listed service only through addresses it already has.

Only the profile's delay, jitter, loss, speed limit, reordering, duplication and
corruption settings are used. Its website list and random outages are not. A
service list uses the saved profile as it is, so random variation in that
profile is not rerolled. All affected devices share one speed limit for each
list in the same way the general profile shares its limit. Delay, jitter and
loss apply to each packet.

Matching is by destination address. A small service on the Pi reads the DNS
answers that leave the Pi towards the test Wi-Fi. When an answer names a listed
hostname or one of its subdomains (or an alias that leads to one) its addresses
join the list within a moment. The Pi also resolves the hostnames of every
enabled list about once a minute for devices that cached an answer earlier.
Addresses stay in a list for six hours after they were last seen and a new plan
starts with the addresses already known. A list holds up to 100 hostnames and
remembers up to 1024 addresses. A client that sends its own fake DNS answers to
the Pi teaches it nothing. The browser proxy also checks the lists first by
exact hostname. Browser tunnels to a listed service are not counted in the
device's data counters because a list belongs to no single device.

A hostname also matches its subdomains and can only be in one enabled list.
A list may hold a name and its own subdomain. Other sites on the same server
address as a listed service get the list's profile too and skip the device's
own outages and website rules the same way. The catalogue is a starting point
so verify a real session and add the hostnames it actually uses. The first
connection to a service that follows the first DNS answer by a few milliseconds
may escape the profile. Encrypted DNS on a client (such as iCloud Private Relay
or DNS over HTTPS), VPNs and fixed server addresses are not covered. One applied
plan supports at most 15 combined devices, service rules and service lists.

### General profile

Choose a saved profile under **General profile** then click **Apply to all**.
It applies immediately to every unprotected device on JitterSim Wi-Fi,
including clients without saved assignments and clients that connect later.
There is no device selection, schedule, duration or usage quota to configure.

The profile's upload and download speed limits are shared only by affected
devices. Clients in **Unaffected by profiles** bypass those limits completely
and do not consume that shared allowance. They also bypass the profile's delay,
loss, website rules and random outages. Separate global access rules still
apply to all clients.

While the general profile is active it takes priority over saved device,
group, schedule, timer, progression, browser service and quota profile rules.
Service lists still take priority over the general profile for their services.
Those settings remain saved. Quota charging pauses during this mode. Timers
and schedules keep their original times. The traffic chart shows the shared
limit and combined affected traffic rather than an individual device limit.
Random changes apply to the network together. Their choices are not added to
the per-device random website history.

**Stop general profile** resumes the saved device rules that are current at
that moment. **Panic** or **Reset network** turns all profiles off while keeping
the selection. **Apply to all** or **Apply current configuration** resumes it.
A restart restores the last apply or reset. Protected clients remain protected
through reconnects and IP changes.

### Connected clients and unaffected devices

Open **Connected clients** after checking the admin token. This panel shows
devices on the Pi's test Wi-Fi. Devices on the router's Wi-Fi are outside this
list. A client needs a verified connection and an IPv4 address before a profile
or group can be assigned.

Choose a profile beside a client then click **Assign profile** or **Apply
profile**. Choose an existing group then click **Add to group**. For a client
without a saved assignment, that group needs a profile override. The **Group
devices** form can also select several connected clients. A client can belong
to one group. A base profile can be overridden by a group, schedule, timer,
progression or quota. The save message explains the current override.

Click **Make unaffected** to protect a connected client from every profile,
random outage, profile website rule and device or service quota. Its traffic
also stops consuming shared group quotas. Its saved assignments stay in place
and resume when protection is removed. Global MAC and website access rules
still apply. Protection does not grant dashboard or SSH access.

The **Unaffected by profiles** list keeps protection when a client disconnects
or changes IP. It supports up to 256 saved Wi-Fi MAC addresses. Use **Add an
offline device** for a device that is not connected. If its private Wi-Fi MAC
changes, add the new address. When protection is configured, unverifiable
clients have their profile rules skipped and the dashboard reports a warning.
If applying a change fails, the dashboard reports that settings were saved
but network protection is not confirmed. Retry **Apply current configuration**
after fixing the helper error.

## Network layout

```text
Vodafone router ── Ethernet ──> Raspberry Pi
                                  │
                                  ├── JitterSim test Wi-Fi
                                  └── JitterSim dashboard
```

Only devices on the JitterSim test Wi-Fi are affected. Devices that stay on the
router Wi-Fi bypass JitterSim. The test Wi-Fi uses IPv4 sharing. Device rules
and quotas match IPv4, while the browser proxy can connect to public IPv4 or
IPv6 hosts for managed HTTPS traffic.

Any household device can join the JitterSim Wi-Fi without a JitterSim device
record. The DHCP service and ordinary internet forwarding run in
NetworkManager, independently of the dashboard and network helper. A network
plan normally marks devices you add in the dashboard. Other clients use a
normal fair queue. When a general profile is active every unprotected client
receives it instead. Protected clients keep their normal traffic path.

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
not the speed a device successfully receives. Dashed lines show manually
configured ISP plan limits and the selected device's applied default-queue
bandwidth limits. The initial ISP download reference is 100 Mbit/s and upload
is unset. Edit these values to match the connection. They are graph references
only and do not change network traffic.

The ISP plan limit is shared with other home devices and is not a live measure
of spare bandwidth for JitterSim. Select one device to see its default-queue
limits. Service-specific queues can use different limits, so those profile
lines do not represent every flow from that device. Run a speed test on the
connected device to check its experienced speed.

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

For control from another location, join the Pi and laptop to your Tailscale
network and privately serve the dashboard over the Pi's Tailscale name. Keep
the admin token. This does not route the laptop's general internet traffic
through the Pi. The [Pi guide](deploy/raspberry-pi/README.md#remote-control-with-tailscale)
has setup, access rules, checks and removal steps. Tailscale's Personal plan
is [free for eligible home use](https://tailscale.com/pricing).

On the Pi, open the dashboard over HTTPS at `https://jittersim.local:8080` or
the Pi's Ethernet address. The installer publishes the local name through
Avahi on the management network. If the router does not carry local name
traffic between Ethernet and Wi-Fi, use the Ethernet address. The installer
creates a local certificate and prints its SHA-256 fingerprint. Verify that
fingerprint before trusting the certificate in your browser. A DHCP address
change may require a new certificate. HTTPS protects the browser-to-Pi
connection, but the TG3442DE's own local web interface still uses HTTP between
the Pi and router.

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

## Project layout

- `src/jittersim/` — dashboard, profiles, schedules and network helper
- `deploy/raspberry-pi/` — Pi services and installation files
- `tests/` — automated checks
- [`ROADMAP.md`](ROADMAP.md) — future work and known limits
