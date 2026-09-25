# Raspberry Pi deployment

This directory contains the versioned deployment pieces for the Pi appliance.
The installer keeps credentials out of the repository and generates them on
the Pi the first time it runs.

## Security boundary

The Pi has two network roles by default. Create or edit
`/etc/jittersim/runtime.env` before installing if the interface names differ:

- `eth0` is the management and router-facing interface.
- `wlan0` is the isolated test-network interface.

The dashboard listens over HTTPS on the management network. The nftables
service blocks SSH and dashboard traffic arriving from the test interface, so a person who
receives the test Wi-Fi password cannot use the Pi as an administration
endpoint. It also blocks private IPv4 destinations and local IPv6 addresses
forwarded from the test interface to the uplink, including the router's web
interface. The generated test Wi-Fi uses IPv4 sharing and does not offer client
IPv6. Browser proxy tunnels may connect to public IPv4 or IPv6 hosts.
Dashboard requests and private read routes require the generated admin token.

Do not expose port 8080 or SSH to the public internet. Keep the Pi's router
connection on `eth0` and use the dashboard from the management network.

## Install or update

Run the installer as root from a checked-out source tree or an extracted source
archive:

```sh
sudo deploy/raspberry-pi/install.sh
```

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
prints the certificate fingerprint.

Open the dashboard at `https://jittersim.local:8080` or use the Pi's Ethernet
address. Before trusting the local certificate in a browser, compare its
SHA-256 fingerprint with the value printed by the installer. The certificate
includes the Pi's current Ethernet address. If that address changes, run the
installer again and verify and trust the newly printed certificate.

After an update, the dashboard supports schedules, one-shot profile timers,
progression sessions, MAC rules, website rules, traffic observations and
randomized profile changes. A progression can move a device from one profile
to another over time or as its DNS query count grows. It can also block more of
its selected domains as it advances.
The helper applies these rules on the Pi and reset pauses scheduled changes
before removing them. A timer temporarily takes priority for its device then
returns to the normal schedule or profile when it expires.
After a service restart, JitterSim restores the last applied plan or completes
the last reset. A fresh installation with no prior action leaves the network
unchanged.

Progression traffic mode is a local DNS-query signal. It is useful for testing
changing behavior but it does not measure every packet or download byte.

## Household Wi-Fi trial

The AP can accept clients that have no dashboard device record. They get a
DHCP address and ordinary internet access. Only devices with a record and an
active profile get device-specific traffic marks. A plan keeps unconfigured
clients on a fair queue even when one test client has delay or loss. The AP,
DHCP and ordinary forwarding belong to NetworkManager, so they do not depend
on the dashboard or helper process staying up. Leave global website blocks and
MAC allowlists off during this trial because those controls can affect clients
without a device record.

This remains a routed, isolated test network. Wi-Fi clients cannot reach
private addresses behind the Ethernet router. Wired clients cannot normally
start connections to Wi-Fi clients through the Pi's NAT. Cross-network
discovery, some casting and local equipment may fail. Client IPv6 is disabled.
The Pi dashboard is blocked from this Wi-Fi, so use the management Ethernet
network or the private Tailscale address for control.
There is no automatic fallback if the Pi, its power supply, its radio or its
Ethernet link fails. The built-in radio's coverage and load capacity need
measurement on the actual Pi. Keep the router Wi-Fi on during the trial.
The first household trial uses internet access only. Reaching devices on the
router's wired network would require a separate network design.

1. Install while connected to the Pi over Ethernet. Confirm the installer
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
4. Apply the configuration in the dashboard. Try the selected service and an
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

Open the `https://...ts.net/` address printed by Serve from the Mac. Enter the
JitterSim admin token. Test status, a profile apply and Reset network from a
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

Keep SSH access through the Pi's management network. To clear project-owned
traffic rules after a dashboard problem:

```sh
sudo systemctl stop jittersim-dashboard.service
sudo /opt/jittersim/.venv/bin/jittersim-reset
sudo systemctl start jittersim-dashboard.service
```

The reset command refuses to run while the dashboard is active. It asks the
helper to remove only JitterSim's rules then records the reset so a restart
does not reapply the previous plan. If reset reports an error, leave the
dashboard stopped and inspect the helper log before starting it again.

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
https://jittersim.local:8080
```

From a device connected to `JitterSim-Test`, the dashboard and SSH ports must
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
