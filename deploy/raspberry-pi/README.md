# Raspberry Pi deployment

This directory contains the versioned deployment pieces for the Pi appliance.
The installer keeps credentials out of the repository and generates them on
the Pi the first time it runs.

## Security boundary

The Pi has two network roles:

- `eth0` is the management and router-facing interface.
- `wlan0` is the isolated test-network interface.

The dashboard still listens on the Pi so the operator can reach it from the
management network. The nftables service blocks SSH and dashboard traffic
arriving from `wlan0`, so a person who receives the test Wi-Fi password cannot
use the Pi as an administration endpoint. State-changing API requests also
require the generated bearer token.

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
credential files so an update does not silently invalidate access.

After an update, the dashboard supports schedules, MAC rules, website rules,
traffic observations and randomized profile changes. The helper applies these
rules on the Pi and reset pauses scheduled changes before removing them.

Read the credentials only on the Pi:

```sh
sudo cat /etc/jittersim/control.env
sudo cat /etc/jittersim/ap.env
```

Never commit those files or paste their contents into a chat or ticket.

## Verify the boundary

From the management network, the dashboard should be reachable at:

```text
http://jittersim.local:8080
```

From a device connected to `JitterSim-Test`, the dashboard and SSH ports must
not be reachable. The device should still receive internet access through the
Pi gateway.

## Vodafone router note

For a Vodafone TG3442DE, the router Wi-Fi must be turned off manually in the
router web interface if the goal is to make the Pi test network the only Wi-Fi
network in use. JitterSim does not log in to the router, copy its password or
change its Wi-Fi settings.

The dashboard shows the Pi's Ethernet connection label and default gateway.
That label is local to the Pi and is not the router's Wi-Fi name. Ethernet does
not expose the router Wi-Fi name or password, so copy both values manually from
the router settings before entering them in the Pi test Wi-Fi form.

The dashboard can change the Pi's own test SSID and password. This does not
change the upstream router and the new test password is not stored in the
JitterSim database.
