# Raspberry Pi deployment

This directory contains the versioned deployment pieces for the Pi appliance.

Install the helper and dashboard from the same release. The sidebar's Router
Wi-Fi switch needs the helper's read-only status action. It refreshes while
the dashboard is visible and uses background operations for slow router
changes. An older helper shows unavailable status. No router password is
saved on the Pi by these checks.
The installer keeps credentials out of the repository and generates them on
the Pi the first time it runs.

## Security boundary

The Pi has two network roles by default. Create or edit
`/etc/jittersim/runtime.env` before installing if the interface names differ:

- `eth0` is the management and router-facing interface.
- `wlan0` is the isolated test-network interface.

The dashboard listens over HTTPS on the management network and test Wi-Fi.
Test clients can reach a limited sign-in page. Private data and controls
require the admin token plus saved device access on test Wi-Fi. Choose
**Check token** then **Allow this device** to register the requesting computer.
Anyone holding the admin token may register their own device. The saved
hardware address is not a second password. The nftables service keeps SSH
and mDNS traffic blocked on the test interface. The service blocks
private IPv4 destinations and local IPv6 addresses forwarded from the test
interface to the uplink, including the router's web interface. The generated
test Wi-Fi uses IPv4 sharing and does not offer client IPv6. Browser proxy
tunnels may connect to public IPv4 or IPv6 hosts.
Dashboard requests and private read routes require the generated admin token.

Do not expose port 8080 or SSH to the public internet. Keep the Pi's router
connection on `eth0`. Management access remains available for recovery.

Only local dashboard traffic avoids profiles and client blocks. Ordinary
internet traffic still follows the assigned profile. The access list is kept
in root-owned `/etc/jittersim/dashboard-access.json` and survives reboot and
upgrades. Include it in private configuration backups. The SQLite dashboard
download does not contain it. See the [dashboard access guide](../../README.md#dashboard-access-from-pi-wi-fi)
for the Mac command and Pi recovery commands.

## Install or update

Run the installer as root from a checked-out source tree or an extracted source
archive:

```sh
sudo deploy/raspberry-pi/install.sh
```

This Pi release replaces its source folders on upgrade so removed modules do
not remain in the installed package. The installer installs the access
recovery, DNS, update and Wi-Fi units and enables the recovery and Wi-Fi units.
`jittersim-ap.service` starts the test Wi-Fi at boot after the management
firewall and the access restore. An installer run never restarts a running
Wi-Fi connection.
An active release update must finish before installation can proceed. Keep a
private backup of `/opt/jittersim`, `/etc/jittersim`, `/var/lib/jittersim`,
the current service definitions and NetworkManager connections before changing
releases. A software rollback must restore those service definitions and their
previous enabled state as well as code and settings.

If the source tree is stored elsewhere, pass its path as the first argument:

```sh
sudo /path/to/jittersim/deploy/raspberry-pi/install.sh /path/to/jittersim
```

The installer creates the `jittersim-ap` Wi-Fi connection with DHCP, IPv4
sharing and address `192.168.50.1/24`. DHCP gives out
`192.168.50.100`–`192.168.50.199` so managed clients can use stable manual
addresses outside that range. This requires NetworkManager 1.50 or newer.
Before installing packages or changing services, the installer checks the
NetworkManager version, Ethernet route, AP interface and subnet overlap. It
also checks that Wi-Fi is enabled and does not replace an active Wi-Fi
connection used for another purpose. On a Pi 3 Model B+, set the correct WLAN
country before installation with `sudo raspi-config` under Localisation
Options. Raspberry Pi documents this as a prerequisite for its dual-band
radio. Use a good 5 V/2.5 A power supply. See the
[Pi 3 B+ specifications](https://www.raspberrypi.com/products/raspberry-pi-3-model-b-plus/)
and [wireless setup guide](https://www.raspberrypi.com/documentation/computers/configuration.html).
The installer also installs `iw`, which the dashboard uses to distinguish
devices currently connected to the access point from old neighbor entries.

Raspberry Pi OS Lite identifies the edition but not the release. Check the
release and NetworkManager version on the Pi with `cat /etc/os-release` and
`nmcli --version`. The installer requires NetworkManager 1.50 or newer. It then
installs a virtual environment, writes the systemd units, enables the
management firewall and starts the browser proxy. Installed code is root-owned.
An update does not restart an already active `jittersim-ap`. If saved AP
settings changed, restart it during a planned maintenance window. The
installer preserves existing credential files and an existing access-point
password so an update does not silently invalidate access. The values in
`ap.env` are the initial credentials. Later dashboard changes are stored in the
network connection. The installer also creates a local HTTPS certificate. Its
private key is readable only by root and the JitterSim service. The installer
prints the certificate fingerprint. It also links the `jittersim` command into
`/usr/local/bin`.

Open the dashboard at `https://jittersim.local:8080` or use the Pi's Ethernet
address. Avahi publishes the name on the local network and the installer
refreshes it when the management interface receives a new address. Routers do
not normally carry mDNS between separate networks. If the name does not
resolve from the management Wi-Fi, use the Pi's Ethernet address. Before
trusting the local certificate in a browser, compare its SHA-256 fingerprint
with the value printed by the installer. The certificate includes the Pi's
current Ethernet address and the test Wi-Fi address `192.168.50.1`. If the
Ethernet address changes, run the installer again and verify and trust the
newly printed certificate.

After updating from a version before September 2026, choose **Reset network**
once. Older versions left duplicate traffic filters behind and a reset removes
them.

An update also upgrades the saved schedules so they can apply to a group or to
everyone else. Older versions can't read those schedules, so before going back
to one, delete them or restore a backup from before the update.

The certificate is named after the Pi (`<pi-name>.local`) and also covers
`jittersim.local` and the current Ethernet address. If the host name or the
address changes, run the installer again and verify and trust the newly
printed certificate.

Open **Admin token** in the sidebar and choose **Check token** to load the
dashboard: `sudo grep JITTERSIM_ADMIN_TOKEN /etc/jittersim/control.env` shows
the token. The token is kept in the page only, or for 24 hours when you tick
**Remember**. Changing the token in `control.env` and restarting the dashboard
locks every open page.

### Client isolation

The access point isolates its clients: devices on the test Wi-Fi can reach the
internet through the Pi, but not each other. A visitor's phone can't see your
laptop's shared folders or other devices on the test Wi-Fi. Casting and screen
sharing between test Wi-Fi devices stop working too. New installs call the
test Wi-Fi `JitterSim`. Existing installs keep their name. You can change it in
the dashboard.

Isolation starts when the test Wi-Fi next restarts. The installer doesn't
restart a running test Wi-Fi, so after updating, reboot the Pi or run the
command below. Test Wi-Fi devices drop off for a few seconds.

```bash
sudo nmcli connection up jittersim-ap
```

To allow test Wi-Fi devices to reach each other, add this line to
`/etc/jittersim/ap.env`, run the installer again and restart the test Wi-Fi
the same way:

```text
JITTERSIM_AP_ISOLATION=no
```

After an update, the dashboard supports schedules, one-shot profile timers,
progression sessions, MAC rules, website rules, traffic observations and
randomized profile changes. A progression can move a device from one profile
to another over time or as its DNS query count grows. It can also block more of
its selected domains as it advances.
The helper applies these rules on the Pi and reset pauses scheduled changes
before removing them. A timer temporarily takes priority for its device then
returns to the normal schedule or profile when it expires.
After a service restart, JitterSim restores the last applied plan or completes
the last reset. A saved change that the running scheduler did not need to apply
clears that record so JitterSim is then not running after a restart until you
choose Start with saved settings or Reset network. A fresh installation with no
prior action leaves the network unchanged.

The **General profile** control applies one saved profile to every unprotected
test Wi-Fi client, including later arrivals. Its speed limit is shared by
affected clients only. Protected clients use separate normal queues and do
not consume that allowance. Stopping it resumes current saved device rules.
Panic or Reset pauses all profiles while preserving the selection.

Updates start the helper, ensure the test access point is active then restart
the browser proxy before the dashboard restores saved traffic rules. This
keeps the new plan format available to both processes. Before rolling back to
a version without general profiles, reset and verify normal traffic under
the newer version so the older dashboard restores the reset state.

## Device visibility features

Three dashboard features ask the root helper for new actions. Traffic discovery
needs `protected_dns_capture` so protected devices are skipped. The router Wi-Fi
copy buttons need `router-wifi-credentials`. The **Impact** card in Diagnostics
needs `queue-stats`. An update restarts the helper so all three arrive
together. If the dashboard still says the helper needs updating after an update,
restart the services or run the installer again:

```sh
sudo systemctl restart jittersim-helper.service jittersim-dashboard.service
```

Traffic discovery refuses to scan while protected devices exist and the helper
cannot skip them. It never captures protected traffic as a fallback.

Check the features on the Pi after an update:

1. Open Traffic discovery and scan. Device names appear instead of bare
   addresses. Add a phone to **Unaffected by profiles** then scan again. The
   phone's lookups disappear and its row says `Traffic is not monitored`.
2. Open a group such as ImmoScout24 or Netflix. **Block all** then **Unblock
   all** change every name in one step.
3. Give a phone a profile with 10% loss and a 2 Mbit/s cap. Open Diagnostics
   and watch the **Impact** card while the phone loads a video. The loss line
   and the cap line fill in and the verdict turns to `Affected in the last
   minute`.
4. Enter the router admin password then press **Copy Wi-Fi name** and **Copy
   Wi-Fi password** in the sidebar. Paste each one somewhere harmless to check
   then copy something else so the password does not stay on the clipboard.

## Connected device details

The dashboard lists Wi-Fi clients currently associated with the Pi. It joins
the station list to IPv4 neighbors and NetworkManager's local DHCP lease file
at `/var/lib/NetworkManager/dnsmasq-<interface>.leases` (normally
`dnsmasq-wlan0.leases`). The helper reads this file only for the configured
test interface and ignores it when it is missing or larger than 64 KiB. The
DHCP name can be missing or chosen by the client. Type suggestions come from
clear words in that name and should be treated as a hint.
If the station command is unavailable, neighbor entries are marked with an
unknown connection status and cannot be used to assign a profile.

You can save a display name and type from the dashboard. These labels are kept
in the local database, included in its backup and keyed to the client's current
Wi-Fi MAC address. Private Wi-Fi addresses may change and cause a device to
appear with a new label entry. Clearing a saved label removes it from the
database. Saved labels for devices that are offline can also be removed in the
dashboard. The label list is limited to 256 entries. This feature does not scan
devices, look up manufacturers online or collect browser activity.

Progression traffic mode is a local DNS-query signal. It is useful for testing
changing behavior but it does not measure every packet or download byte.

## Household Wi-Fi trial

The AP can accept clients that have no dashboard device record. They get a
DHCP address and ordinary internet access. Only devices with a record and an
active profile get device-specific traffic marks. A plan keeps unconfigured
clients on a fair queue even when one test client has delay or loss. The AP,
DHCP and ordinary forwarding belong to NetworkManager, so they do not depend
on the dashboard or helper process staying up. Leave global website blocks,
MAC allowlists and the everyone-else profile off during this trial because
those controls affect clients without a device record. With an everyone-else
profile, every household device that isn't saved or on the Unaffected devices
list gets that profile.

This remains a routed, isolated test network. Wi-Fi clients cannot reach
private addresses behind the Ethernet router. Wired clients cannot normally
start connections to Wi-Fi clients through the Pi's NAT. Cross-network
discovery, some casting and local equipment may fail. Client IPv6 is disabled.
Use saved dashboard access for control from this Wi-Fi. Router management
Wi-Fi and a configured private Tailscale address remain recovery options.
There is no automatic fallback if the Pi, its power supply, its radio or its
Ethernet link fails. The built-in radio's coverage and load capacity need
measurement on the actual Pi. Keep the router Wi-Fi on during the trial.
The first household trial uses internet access only. Reaching devices on the
router's wired network would require a separate network design.

1. Install from the router management network or a local Pi terminal. Confirm the installer
   preflight passes and keep the router's Wi-Fi name available for recovery.
2. Join an unconfigured laptop and another household device to the JitterSim
   AP. Check automatic IP and DNS, web browsing, video and the local services
   you use. Record anything that fails before moving more devices.
3. Add just one test device with a delay profile. Repeatedly apply, change and
   reset its profile while the unconfigured laptop runs a long download and
   video call. Verify it remains connected and its traffic stays normal.
4. Restart the dashboard and helper then reboot the Pi. Check that both clients
   reconnect and ordinary traffic works. Watch `journalctl -u NetworkManager`
   for AP or DHCP errors. Repeat under normal evening household load.
5. Test every printer, TV, speaker, console, home automation device and remote
   work connection you rely on. A device working on the router Wi-Fi does not
   prove it will work through this separate subnet. If any service needs the
   router LAN, leave it on the router Wi-Fi while a suitable household network
   design is chosen.

Do not turn off the router Wi-Fi until the trial succeeds on the actual Pi for
the number of devices and physical coverage you need. Keep a wired admin path
and the router Wi-Fi recovery route. The [implementation plan](../../docs/implementation-plan-household-access-point-2026-09.md)
records the remaining network-design decision.

## Browser service profiles on a Mac

1. Connect the Mac to the JitterSim test Wi-Fi. In macOS Network → Wi-Fi →
   Details → TCP/IP, give this network a manual address such as
   `192.168.50.10`, subnet mask `255.255.255.0` and router `192.168.50.1`.
   Set DNS to `192.168.50.1`. Use a distinct address from
   `192.168.50.10`–`192.168.50.99` for each managed client. The DHCP pool
   starts at `.100`. Add the Mac's fixed address as a device in the dashboard
   and select its ordinary profile. See [Apple's TCP/IP steps](https://support.apple.com/en-au/guide/mac-help/mh141292/mac).
2. Create a **Browser service profile** for the Mac. Enter one or more HTTPS
   hostnames per line and choose the profile to apply to them. You can group
   LinkedIn, Netflix, Google, Microsoft and YouTube into one assignment or
   save separate assignments for different profiles. Add the hostnames each
   site actually uses in your test. Assignments on the same device cannot have
   overlapping names.
3. In a dedicated Firefox test browser, open its [connection settings](https://support.mozilla.org/en-US/kb/connection-settings-firefox)
   and select manual proxy configuration. Set the HTTPS proxy to
   `192.168.50.1` port `3128`. Leave HTTP and SOCKS proxies empty. This keeps
   the setting inside Firefox. For Safari or Chrome, macOS offers **Secure Web
   Proxy (HTTPS)** under System Settings → Network → Wi-Fi → Details → Proxies.
   That system setting can also affect other apps that honor it.
4. Choose **Start with saved settings** in the dashboard or **Re-apply saved
   settings** if JitterSim already runs. Try the selected service and an
   unrelated HTTPS site in that browser. Use a simple profile with a visible
   delay for the first check. A shared destination address is safe because the
   proxy classifies each tunnel by its requested hostname. To check the proxy
   directly from a test client, run:

   ```sh
   curl -x http://192.168.50.1:3128 -I https://www.youtube.com/
   curl -x http://192.168.50.1:3128 -I https://example.org/
   ```

   A successful CONNECT confirms that the proxy path works. Compare measured
   timing on the client to confirm the profile's effect. Applying a new plan
   closes existing proxy tunnels, so reload open test pages. The Pi logs rejected
   tunnels without logging full browser requests:

   ```sh
   sudo journalctl -u jittersim-browser-proxy.service -n 80 --no-pager
   ```

Only HTTPS CONNECT tunnels with named hosts receive browser service profiles.
Traffic that bypasses the proxy keeps the ordinary device profile. The proxy
accepts only test-network clients, public destinations and port 443. It
preserves active device outage and website address blocks for proxied traffic.
Web pages often contact media, sign-in or delivery hosts beyond the first
domain. Add the hosts needed for the test rather than treating a brand name as
automatic coverage. A VPN or native app is outside this browser feature.
Device traffic counters include proxy tunnels. Service byte and time quotas
do not count those tunnels because their direct-traffic rules use destination
addresses. Disable the browser proxy setting when finished.

## Service lists

A service list shapes traffic to chosen services for every affected client.
It needs no proxy or setting on the client. Create one in the dashboard under
**Schedules and limits** in the **Service lists** card. Use a simple profile with a visible delay for the first
check.

1. Choose **Start with saved settings** in the dashboard. Connect a test client
   that is not in **Unaffected by profiles** and open a listed service and then
   an unrelated site. Only the listed service should show the profile's delay.
2. Check the set the Pi keeps for the list. The set name is `svc_` followed by
   the list's queue number (the first list in a plan with no devices is
   `svc_1`):

   ```sh
   sudo nft list set inet jittersim svc_1
   sudo nft list ruleset | grep service-list
   ```

   The set should hold addresses for the listed hostnames within a minute of
   applying. The `service-list` rules show packet and byte counters for both
   directions.
3. Compare with an unaffected client. Its traffic to the same service should
   stay normal.

The `jittersim-service-watch` service reads the DNS answers that leave the Pi
towards the test Wi-Fi and passes listed addresses to the helper. It runs as
the `jittersim` user with only the packet capture capability. The Pi also
resolves every enabled list's hostnames about once a minute and the helper
keeps each address for six hours after it was last seen. Check the watcher
with:

```sh
sudo systemctl status jittersim-service-watch.service
sudo journalctl -u jittersim-service-watch.service -n 50 --no-pager
```

A listed service that uses many hostnames may need more names than the
catalogue holds. Add the hostnames you see in the dashboard's traffic
discovery. A reset removes the sets together with every other rule. Encrypted
DNS on a client hides its lookups from the watcher so the client may reach
addresses the Pi never saw.

## Remote control with Tailscale

This is optional. [Tailscale's Personal plan](https://tailscale.com/pricing)
is free for eligible non-commercial home use. Check its current terms before
signing up. Follow the official [Pi/Linux installation](https://tailscale.com/docs/install/linux)
and [Mac installation](https://tailscale.com/docs/install/mac) steps. On the Pi:

```sh
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
tailscale status
```

Sign in to the same private Tailscale network on the Mac. Confirm both devices
appear in `tailscale status`. In the Tailscale admin console, grant the Mac's
device address access to the Pi's device address on `tcp:443`. Replace the
example addresses with those shown for your two devices:

```json
{
  "grants": [
    {"src": ["100.101.102.103"], "dst": ["100.104.105.106"], "ip": ["tcp:443"]}
  ]
}
```

Merge that entry into your existing tailnet policy. Remove any broader grant
that would also permit other devices to reach the Pi, since grants add access
rather than override one another. Keep any separate permissions your network
needs. See the [Tailscale grants guide](https://tailscale.com/docs/reference/syntax/grants).

On the Pi, privately publish the already protected local dashboard. This
forwards to its self-signed local certificate and gives the browser a valid
private Tailscale HTTPS name:

```sh
sudo tailscale serve --bg --https=443 https+insecure://127.0.0.1:8080
sudo tailscale serve status
```

If prompted, enable HTTPS for the private network in the Tailscale admin
console. Serve prints the private dashboard address.

Open the `https://...ts.net/` address printed by Serve from the Mac. Sign in
with the JitterSim admin token. Test status, a profile apply and Reset network from a
separate internet connection such as a friend's Wi-Fi. Confirm the local
dashboard still opens at home. Tailscale Serve stays private to the tailnet.
Turn off the test browser's `192.168.50.1:3128` proxy setting before leaving
the test Wi-Fi. That proxy is for traffic tests at home, while Tailscale serves
the dashboard from anywhere the private connection works.
Do not enable Funnel, router port forwarding or a Tailscale exit node. The
laptop's normal internet traffic remains on its current connection.

To stop remote dashboard access without changing local control:

```sh
sudo tailscale serve --https=443 off
sudo tailscale serve status
```

If a laptop is lost, remove its device from the Tailscale admin console and
revoke its grant. If home internet or the Pi is down, remote control is
unavailable. See the [Serve command guide](https://tailscale.com/docs/reference/tailscale-cli/serve)
for current command behavior.

## Recover over SSH

To recover from a copied router network name, run `sudo jittersim wifi reset`
on the Pi through its management connection or a local terminal. Add `--yes`
to confirm without a prompt. The command restores `JitterSim` and keeps the
current password and security type. It restarts test Wi-Fi and needs the
helper but works with the dashboard stopped. The dashboard has the same
**Reset Wi-Fi name** control on its **Router and Wi-Fi** page.
Administration remains unavailable from the test network.

**Copy router Wi-Fi to Pi** first checks the source band and personal
security type. Its confirmation shows the detected mode. Name, password and
security come from the same band. It reads the router again before copying
and rejects a changed name, band or mode. It never displays the Wi-Fi password.
Use the router's Wi-Fi password when a test device asks for a WPA password.

WPA, WPA2, WPA3, WPA/WPA2 and WPA2/WPA3 have distinct settings and active-radio
checks. Older WPA requires confirmation of its weak security. A mode that
cannot be verified on the Pi fails and restores the previous profile. Open,
WEP, enterprise and unknown modes are rejected. Numeric firmware codes are
accepted only where their meaning is known. Older firmware must expose a
recognized security field to support copying.

A fresh installation and new manual Wi-Fi settings use WPA3 with AES.
Installer upgrades preserve the existing mode. Verify the advertised security
and a client connection on each target Pi before relying on a mode. Mixed
WPA2/WPA3 must allow both kinds of client. Container tests do not prove this.

On the installed Pi with NetworkManager 1.52.1, checks on 30 September 2026
verified running WPA2 and WPA3 plus name reset preserving each mode. WPA,
WPA/WPA2 and WPA2/WPA3 failed active-radio checks and restored the previous
profile. A client connection for each mode remains a separate acceptance check.

Imports keep a NetworkManager recovery profile named `jittersim-ap-recovery`
with the original credentials. NetworkManager protects its secret storage.
The copy disables automatic connection of that recovery profile and removes
it after success. If a copy fails, one bounded restoration attempt restores
it. The helper attempts pending recovery once when it starts. If that fails
it leaves management controls available and retains the recovery copy for
repair. Do not delete a remaining recovery profile before fixing the Wi-Fi.
Name recovery also reports whether its one restoration attempt succeeded.

Code rollback alone does not restore NetworkManager settings. A previous
version may force WPA3 during installation. Restore a compatible working
profile through management access before installing that older version.

Keep SSH access through the Pi's management network. If the dashboard cannot
be reached or misbehaves, turn off all experiment effects and household restrictions:

```sh
sudo jittersim normal-access
```

The command records the stopped state before asking the helper to remove
JitterSim's effects and household restrictions. Saved lists remain available.
A running dashboard notices the reset and stops scheduled changes. Use
`sudo jittersim reset` to keep household restrictions active while stopping
experiments. The older `jittersim-reset` command has the same limited scope.
Choose **Start with saved settings** to resume experiments. Re-enable
household access rules separately when ready.

`sudo jittersim status` shows the last recorded network action and whether the
helper answers. If reset reports an error, read the helper log as described
below before applying again.

## Headless commands

To recover from a copied router network name:

```sh
sudo jittersim wifi reset
sudo jittersim wifi reset --yes
```

Use either command on the Pi through a management connection or local terminal.
The first asks for confirmation. The second confirms without a prompt. Both
restore the name to `JitterSim`, keep the current password and restart test
Wi-Fi. They require the helper service but work with the dashboard stopped.
Saved profiles and household access rules are preserved.

The Setup tab has the same **Reset Wi-Fi name** action. It cannot be reached
from the test network. Keep the router Wi-Fi on and a working management route
before copying its name to the Pi. If your computer has already joined the
copied Pi network, reconnect to the router before trying dashboard recovery.

Installation and every Wi-Fi change require WPA3 with AES and protected Wi-Fi
management messages. Updating a running
Pi saves those security settings without restarting active Wi-Fi. Follow the
installer's planned restart instructions to activate them. Runtime changes
activate the new settings and verify WPA3 authentication with required protection
on the active radio before reporting success. Clients need WPA3 support.
A failed name reset restores the previous security rather than silently allowing
weaker authentication.

If name recovery fails, the helper makes one attempt to restore the previous
name and security settings. It reports whether restoration also failed.
Repair `jittersim-ap` through management access if that happens. Rolling back
the code alone does not revert NetworkManager settings.

The CLI can show access status, change the DNS error chance and apply saved
rules without opening the dashboard:

```sh
sudo jittersim access status
sudo jittersim access dns-failure show
sudo jittersim access dns-failure set 15
sudo jittersim access apply
sudo jittersim persistence show
sudo jittersim persistence set on
sudo jittersim persistence set off
sudo jittersim diagnostics
sudo jittersim logs dns --lines 80
```

Gateway persistence defaults to off. The helper checks the configured router
gateway once every five seconds. Three unanswered checks confirm an outage
and two replies confirm recovery. Before the router has answered once, a
missing route or unavailable check is unknown. After that, losing the route
counts as an unanswered check, because a router restart usually drops the
Ethernet link. With persistence on, active profiles stay
in place and the current scheduled plan is restored after the router returns.
With it off, a confirmed outage removes active profiles and keeps them stopped
until you choose Apply after the router is reachable. Saved profiles and
schedules remain. Timers continue to use wall-clock time during an outage.
The preference is saved in settings exports and imports. Gateway health and a
stopped state stay on the Pi. Pi restart behavior is unchanged in this first
version. The check uses ICMP to the configured router, so confirm that the
router answers ping before relying on this setting.

`jittersim persistence show` displays the saved mode and the helper's current
gateway state. If the helper cannot be reached after changing the setting, the
CLI reports that the saved value may not yet be active. Run the command again
once the helper is available.

The chance defaults to 0%. It rejects a share of DNS requests from test Wi-Fi
with a quick network error. It does not change answers for the management
network. A 100% chance asks for confirmation because every new test-network
name lookup will fail. UDP lookups are decided per request and TCP lookups
once per connection. Device or browser DNS caches may keep already resolved
names working until they expire. VPNs, encrypted DNS or direct IP connections
may bypass the setting.

Update commands use the same verified staged release and recovery service as
the dashboard:

```sh
sudo jittersim update check
sudo jittersim update install
sudo jittersim update rollback
```

`update check` reports releases already staged on the Pi. It does not query
the private repository online. Install and rollback ask for confirmation and
briefly interrupt the dashboard and test network. Keep Ethernet or SSH
available for recovery. Run `sudo jittersim --help` or
`sudo jittersim update --help` for command help.

## Troubleshoot a failed apply

If the dashboard reports `network helper apply failed`, read the helper log on
the Pi:

```sh
sudo journalctl -u jittersim-helper.service -n 80 --no-pager
```

The log shows which network step failed, its error output and whether cleanup
completed.

## Credentials

Read the credentials only on the Pi:

```sh
sudo cat /etc/jittersim/control.env
sudo cat /etc/jittersim/ap.env
```

Never commit those files or paste their contents into a chat or ticket.

## Verify the boundary

From the management network, the dashboard should be reachable at:

```text
https://<pi-name>.local:8080
```

Without signing in, it must show only the plain sign-in page. From a device
connected to the test Wi-Fi, the dashboard and SSH ports must
not be reachable and `192.168.0.1` must not be reachable through the Pi. The
device should still receive public internet access through the Pi gateway.
This is an intentional block rather than a password redirect because a
redirect cannot safely intercept HTTPS or router traffic.

## Vodafone router note

For a Vodafone TG3442DE, the dashboard can read the primary Wi-Fi settings and
turn the primary Wi-Fi radios on or off. Enter the router admin password in the
dashboard for one action at a time. The password is not saved. Keep the Pi
connected by Ethernet before changing router Wi-Fi. If a router firmware update
changes its local web interface, use the direct router link or the manual
access-point form. HTTPS protects the browser-to-Pi connection. The router's
local web interface still uses HTTP between the Pi and the router.

The dashboard shows the Pi's Ethernet connection label and default gateway.
That label is local to the Pi and is not the router's Wi-Fi name. The router
controls use the gateway to open the TG3442DE local web interface.

The dashboard can change the Pi's own test SSID and password. This does not
change the upstream router and the new test password is not stored in the
JitterSim database.

## Approved release updates

Install this version once using the normal installer to add the update service.
After that, Setup can check the installed version, install an approved staged
release and restore the previous installation. The Pi stores no GitHub
credentials. An administrator transfers the release over the existing SSH path.

Releases have semantic version numbers: MAJOR.MINOR.PATCH. A new feature raises
MINOR and a fix raises PATCH. A change that needs a reinstall with the installer
or breaks saved settings raises MAJOR. Put the number in `pyproject.toml` and in
`src/jittersim/__init__.py` then mark the reviewed commit with
`git tag vMAJOR.MINOR.PATCH`. A test fails when the tag and the files disagree.

Build a bundle from the tag on the development machine:

```sh
./deploy/raspberry-pi/build-release.sh vMAJOR.MINOR.PATCH /tmp
```

The bundle is named `jittersim-MAJOR.MINOR.PATCH.tar.gz` and the script prints
the version it built. A commit without a tag builds a test build named
`NUMBER.dev+COMMIT` that never looks like a release. A second argument that ends
in `.tar.gz` names the file yourself.

Save the printed SHA-256 checksum with the release record. Stage the version the script printed.
The update refuses to install an older number over a newer one. Use Restore previous
installation to go back on purpose. Transfer that exact
bundle to the Pi using the existing SSH account then stage it as root:

```sh
sudo /opt/jittersim/.venv/bin/python -m jittersim.updates stage /tmp/jittersim-release.tar.gz --version RELEASE_VERSION --sha256 APPROVED_SHA256
```

The checksum must come from the trusted release record. Staging an archive is
approval to run its code as root. Staging rejects unsafe archive paths, links,
wrong checksums, more than 5000 files and excessive compressed or expanded size.
Only the administrator can write the staged files. The dashboard can select
only that staged release and cannot submit a URL, path or shell command.

In Setup, press Check for updates then Install staged release. The operation
runs the release steps of `install.sh`. It first runs the release's network
preflight and kernel egress-hook check. If either fails nothing changes. It
then stops the dashboard, helper and browser proxy. It backs up the
installation, configuration, database, service definitions, the dashboard name
dispatcher script and the `jittersim` command link before installing the
package. It installs the same service files, dispatcher script and firewall
rules as the installer and restarts the services in the installer's order,
including the DNS watcher and the dashboard name publisher. A running test
Wi-Fi is never restarted. It then checks the authenticated HTTPS health
response and helper connection. A failure after installation begins restores
that backup. Allow a brief test-network interruption and keep the Ethernet or
SSH recovery path available. OS packages, credentials, the certificate and
Wi-Fi settings still use the normal installer. The recovery program already on
the Pi runs the update so changed install steps apply from the update after
it. Run the installer once for a release that changes them.

The recovery program uses system Python and is stored outside the app's virtual
environment. A queued operation runs again after reboot. An interrupted install
with a complete recovery copy restores the old installation before another
attempt. Recovery failures remain visible in Setup when the dashboard can run.
Inspect a failure over SSH with:

```sh
sudo systemctl status jittersim-update.service
sudo journalctl -u jittersim-update.service -n 80 --no-pager
```

Restore previous installation replaces settings and usage with the backup made
before that update. This differs from Diagnostics' settings-version restore,
which retains current usage for matching limits. One previous installation and
one staged release are retained. A new successful backup replaces the previous
recovery copy. Ensure enough free disk space for two installation copies plus
100 MiB of working space.

Automated tests exercise file validation and recovery with disposable files.
They do not establish radio coverage, uninterrupted household service or actual
Pi restart behavior. Complete the household and recovery trial on the Pi before
relying on it for everyday access.

## Persistent access rules and normal access

Run the current installer once when upgrading to this release. It provisions
Unbound, connection tracking and the new startup units. A code-only update
from an older installation does not install those system dependencies.
Subsequent release updates retain a recovery copy of both the database and
`/var/lib/jittersim-access` alongside the service definitions.

The installer turns off automatic activation of the NetworkManager AP profile.
`jittersim-ap.service` starts it after the management firewall is loaded and
the saved access rules were tried. The unit only orders itself after those two
units. It does not require them. The test Wi-Fi therefore comes up at boot even
when restoring the access rules failed. In that case the guard table keeps test
traffic closed while the Wi-Fi stays visible and Normal access opens it. The
unit refuses to start without the management firewall. It has no stop action
so restarting the firewall or the access unit during an install or an update
never touches the running Wi-Fi. To take the Wi-Fi down on purpose run
`sudo nmcli connection down jittersim-ap`. The privileged helper owns the
applied access policy.
The dashboard cannot write its files directly. DNS failures keep blocked
traffic blocked. A failed policy recovery closes test-network traffic while
leaving the Ethernet management path available.

The filtered resolver listens on `127.0.0.1` and `192.168.50.1` at port 5335.
The access table redirects test-client DNS there and keeps that port closed to
every other interface. Port 5353 stays free for the Avahi publisher so
`<pi-name>.local` and `jittersim.local` keep resolving from the laptop.

`jittersim-dns.service` is installed but not enabled. The helper starts it
when saved access rules are on, at boot through `jittersim-access-boot` and
on each access apply. It is expected to be inactive while access rules are off.
If it is inactive while they are on, check `sudo jittersim logs access-boot`.

The DNS service checks names and all subdomains with separate views for clients
that have different restrictions. Equivalent clients share a view. Limits are
32 named rules, 64 domains per rule and 512 named domains in total. A further
limit covers expanded device/domain combinations. Queries are not logged.
Existing on-demand DNS observations remain a separate feature.

**Reset network** and `sudo jittersim reset` now clear experiment effects while
preserving household restrictions. Use the sidebar **Panic** button (the same
as **Normal access** under Access rules) or `sudo jittersim normal-access` to
disable both. They pause automatic changes,
remove profiles and disable access rules without deleting the saved lists.
Afterwards only the access table's drop of outside traffic to the private
resolver port 5335 and an empty device table remain.
DNS connection mappings are cleared so a reused resolver connection follows
the new setting. The emergency action does not depend on restarting Unbound.
Management authentication, the private management firewall and Wi-Fi client
isolation are still enabled. Device-side DNS caches can take time to expire.

The standard installation uses NetworkManager. MAC rules block network traffic
on that setup. Wi-Fi admission controls remain unavailable unless a supported
hostapd control socket exists at `/run/jittersim-hostapd/<test-interface>`.
Controller commands are tested with real hostapd. This does not establish
compatibility with a particular Pi radio or prove refused Wi-Fi association.
Changing AP backends requires hardware acceptance before enabling this option.

If startup restoration fails, inspect `journalctl -u jittersim-access-boot`
and `journalctl -u jittersim-helper` over Ethernet. Stop the dashboard and
helper before restoring `/var/lib/jittersim-access` from the release recovery
copy. Then restart `jittersim-access-boot.service` and the helper and dashboard.
Start `jittersim-ap.service` too if the test Wi-Fi is not up. Do not remove the
temporary firewall guard merely to hide a failed restoration.

Settings exports use version 2 so an older reader cannot silently discard the
new site rules. Restoring a release without access-control support is refused
while household controls are enabled. Explicitly disable them first if the
loss of protection is intended. Keep the database and applied access-policy
backup from the same recovery point.

Hardware acceptance still needs two Wi-Fi clients and the actual Pi: verify
client isolation, DHCP and management access, a changed client address,
startup restoration, normal access and any supported association rejection.
