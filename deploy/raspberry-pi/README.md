# Raspberry Pi deployment

This directory contains the versioned deployment pieces for the Pi appliance.
The installer keeps credentials out of the repository and generates them on
the Pi the first time it runs.

## Security boundary

The Pi has two network roles:

- `eth0` is the management and router-facing interface.
- `wlan0` is the isolated test-network interface.

The dashboard listens over HTTPS on the management network. The nftables
service blocks SSH and dashboard traffic arriving from `wlan0`, so a person who
receives the test Wi-Fi password cannot use the Pi as an administration
endpoint. It also blocks private IPv4 destinations and local IPv6 addresses
forwarded from `wlan0` to `eth0`, including the Vodafone router's web interface.
An applied network plan blocks all IPv6 forwarding from the test Wi-Fi because
per-device IPv6 rules are not yet available. Public IPv4 forwarding remains
available. Dashboard requests require the generated bearer token.

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

The installer creates a virtual environment, installs the package, writes the
systemd units and enables the management firewall. It preserves existing
credential files so an update does not silently invalidate access. It also
creates a local HTTPS certificate. Its private key is readable only by root
and the JitterSim service. The installer prints the certificate fingerprint.
It also creates the test Wi-Fi when it is missing and links the `jittersim`
command into `/usr/local/bin`.

After updating from a version before September 2026, choose **Reset network**
once. Older versions left duplicate traffic filters behind, and a reset
removes them.

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

Read the credentials only on the Pi:

```sh
sudo cat /etc/jittersim/control.env
sudo cat /etc/jittersim/ap.env
```

Never commit those files or paste their contents into a chat or ticket.

## Test Wi-Fi, DHCP and NAT

The helper manages the test Wi-Fi through a NetworkManager connection named
`jittersim-ap`. When that connection is missing, the installer creates it on
the test interface with the SSID and password from `ap.env`. It uses
NetworkManager's shared mode, which gives test devices addresses in
`192.168.50.0/24` from DHCP, answers their DNS queries and translates their
traffic to the Pi's Ethernet address. IPv6 is turned off on the test Wi-Fi
because device rules match IPv4.

The installer skips this step when the test interface carries the Pi's default
route, because turning it into an access point would cut off the session.
Connect the Pi by Ethernet and run the installer again. To create the
connection by hand instead:

```sh
sudo nmcli connection add type wifi ifname wlan0 con-name jittersim-ap \
    autoconnect yes ssid JitterSim-Test 802-11-wireless.mode ap \
    802-11-wireless.band bg ipv4.method shared ipv4.addresses 192.168.50.1/24 \
    ipv6.method disabled wifi-sec.key-mgmt wpa-psk wifi-sec.psk 'the password'
```

Check it with `nmcli connection show jittersim-ap`. This step has not yet been
tested on a Pi.

## Emergency reset over SSH

If the dashboard cannot be reached, clear every JitterSim network change from
an SSH session on the management network:

```sh
sudo jittersim reset
```

The command records the reset first, so the dashboard keeps the network reset
after it restarts, then asks the helper to remove JitterSim's rules and queues.
A running dashboard notices the reset, stops scheduled changes and resets once
more in case one of its changes raced the command. Choose **Apply current
configuration** in the dashboard to start again.

`sudo jittersim status` shows the last recorded network action and whether the
helper answers.

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
