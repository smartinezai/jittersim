# JitterSim

JitterSim helps you test how a phone or laptop behaves when its internet
connection is slow or unreliable. A Raspberry Pi creates a separate test
Wi-Fi so experiments do not change your regular home Wi-Fi.

Use JitterSim only on equipment and networks you own or have permission to
test. It is for experiments, not a replacement for your home router.

## What you can try

- Slow, delay or interrupt a test connection.
- Save different settings for one device or the whole test Wi-Fi.
- Schedule changes, run a timer or gradually switch between settings.
- Control device, website and service access, with data and time limits.
- See connected devices, traffic measurements, the effect of a test and a device's speed before and after a profile.
- Follow a saved device to its new private Wi-Fi address, with Ask, Automatic and Undo.
- Choose whether active profiles resume after a router outage.

## Screenshots

<div align="center">
  <img src="docs/screenshots/experience-overview-desktop.png" alt="Desktop dashboard with a test phone connected" width="68%" />
  <img src="docs/screenshots/experience-overview-phone.png" alt="The dashboard on a phone-sized screen" width="27%" />
</div>

*Sample data only; no real phone or home network is shown.*

## Get started on a Raspberry Pi

Use Raspberry Pi OS with NetworkManager 1.50 or newer, Ethernet connected to
your router and a Wi-Fi radio that supports access-point mode. Create a
`jittersim` account with Raspberry Pi Imager. See the
[Pi deployment guide](deploy/raspberry-pi/README.md#install-or-update) for
signed-release installation, network checks and recovery instructions.

After installation, verify the SHA-256 fingerprint printed by the installer
before trusting the dashboard certificate. Then open
`https://netlab.local:8080` or an IP address covered by the certificate. The
generated admin token is on the Pi in `/etc/jittersim/control.env`.

The [feature and operations guide](docs/user-guide.md) explains the dashboard,
network behavior and local developer setup. See the
[release guide](docs/releases/README.md) for publishing a release, the
[roadmap](ROADMAP.md) for known limits and future work, and the
[documentation index](docs/README.md) for the rest of the guides.
