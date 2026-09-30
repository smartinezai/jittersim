# JitterSim

JitterSim helps you see how a phone or laptop behaves when its internet
connection is slow or unreliable. A Raspberry Pi creates a separate test
Wi-Fi. You can try changes on that Wi-Fi without changing your regular home
Wi-Fi.

Use JitterSim only on equipment and networks that you own or have permission
to test. It is for experiments, not a replacement for your home router.

## See it in action

<div align="center">
  <img src="docs/screenshots/experience-overview-desktop.png" alt="Dashboard overview with a test phone connected and a slow profile running" width="68%" />
  <img src="docs/screenshots/experience-overview-phone.png" alt="The connected test phone and its active slow profile on a phone-sized screen" width="27%" />
</div>

*Screenshots use sample data. No real phone, Pi or home network is shown.*

The dashboard lets you choose a test profile for a device, start a timer,
then see which profile is active. Press **Normal access** to stop profiles,
schedules and household blocking when you are done.

### Make some website lookups fail

<img src="docs/screenshots/dns-failure-controls.png" alt="A 20 percent chance of a quick connection error is saved and active for the test Wi-Fi" width="100%" />

Choose a chance from 0% to 100% that a new website name lookup from the test
Wi-Fi gets a quick connection error. At 0%, this effect is off. This first
version shows a site-connection error. It does not send you to the site's home
page or to a random website. Saved browser and device lookups can delay when
you see the effect.

### Choose what happens if the router goes offline

<img src="docs/screenshots/router-outage-persistence.png" alt="The option to keep active profiles through a router outage is enabled" width="100%" />

With this option on, active profiles return when the router connection comes
back. With it off, a confirmed router outage stops active profiles until you
press **Apply current configuration**. Saved profiles and schedules stay
available. This first version covers router outages only. Pi restart behavior
is unchanged.

### Check for a staged update

<img src="docs/screenshots/staged-update-check.png" alt="Automatic checking is on and a verified release staged on the Pi is ready to install" width="100%" />

Automatic checking is on by default and the setting is saved on the Pi. When
you open the dashboard, it checks for a verified release already staged there.
You can turn automatic checking off and use **Check for updates** manually.
Checking does not download or install a release. The Setup tab shows a badge
when the staged and installed release labels differ. The terminal command
`sudo jittersim update check` shows the same status.

## What you can try

- Slow down a test device, add delays, drop some data or simulate short
  interruptions.
- Change a device's profile for a set time or use schedules and scenarios to
  switch profiles automatically.
- Block selected devices or named websites on the test Wi-Fi.
- Set data or time limits and compare connection measurements.
- Use the browser proxy to test different profiles on selected secure
  websites.
- Choose whether profiles stop or resume after a router outage.
- Restore the Pi Wi-Fi name to JitterSim from the dashboard or terminal.

**Reset network** stops active profile effects while leaving saved settings
and household access rules in place. **Normal access** also turns off device
and website blocking. Both keep the test Wi-Fi separated from your home
devices. If the dashboard is unavailable, connect to the Pi over SSH and run
`sudo jittersim normal-access`.

Site rules include subdomains. A VPN, encrypted DNS or direct internet
connection can bypass network-only filtering. Existing connections or cached
website lookups can continue until they expire. Site blocking is not a
replacement for parental controls. Refusing a device's Wi-Fi connection needs
a supported access point and Pi hardware testing.

## Use the terminal

You can check the Pi and change the new settings over SSH without opening the
dashboard:

```sh
sudo jittersim status
sudo jittersim access status
sudo jittersim access dns-failure show
sudo jittersim persistence show
sudo jittersim update check
sudo jittersim diagnostics
sudo jittersim logs dns --lines 80
```

To change a setting, use `sudo jittersim access dns-failure set 15` or
`sudo jittersim persistence set on`. The DNS setting is saved separately from
applying access rules, so run `sudo jittersim access apply` after changing it.
Run `sudo jittersim --help` for the command list. More examples are in the
[Pi installation and recovery guide](deploy/raspberry-pi/README.md).

Use **Reset Wi-Fi name** on Setup or run `sudo jittersim wifi reset` on the Pi.
It restores `JitterSim` and keeps the current password. Confirming the reset
restarts test Wi-Fi, so its clients need to reconnect. Add `--yes` when running
without a prompt. This command needs the local helper but not the dashboard or
its settings database.

## Set up the Pi

1. Connect the Pi to your router by Ethernet and keep the router's Wi-Fi
   available as a recovery path.
2. Use Raspberry Pi OS with NetworkManager 1.50 or newer. Set the correct
   wireless country for the Pi before starting its access point.
3. From the project folder run:

   ```sh
   sudo deploy/raspberry-pi/install.sh
   ```

4. Open `https://<pi-name>.local:8080` or use the Pi's Ethernet address.
   Check the certificate fingerprint printed by the installer before trusting
   it in your browser.
5. Sign in with the admin token. On the Pi, run
   `sudo grep JITTERSIM_ADMIN_TOKEN /etc/jittersim/control.env` to read it.
   Do not expose the dashboard or SSH to the public internet.

The installer preserves your saved Wi-Fi name and password. Use it when
upgrading an older release so it can add new system services. A code-only
update does not install those services.

The test network requires WPA3 with AES and protected Wi-Fi management messages.
Upgrades save the stronger security settings and report when an active network
needs a planned restart. Manual
Wi-Fi changes, router imports and name resets activate the stronger settings.
Devices must support WPA3 to join. The radio is checked for WPA3 authentication
and required protection before runtime changes report success.

Copying your router's Wi-Fi name can make your debugging computer join the Pi
instead. Keep a working management connection before using that action. The
reset button is available through management access. A computer already on the
test Wi-Fi cannot reach the dashboard or SSH.

The test Wi-Fi is separate from your home network. Devices on it cannot reach
your router's private network or each other. Printing, casting and other local
services may not work through it. Test with one device first and keep your
router's Wi-Fi available. See the [Pi installation, recovery and household
trial guide](deploy/raspberry-pi/README.md).

## Run locally

Local mode saves settings and builds plans without changing network traffic.

```sh
python3 -m venv .venv
.venv/bin/pip install '.[dev]'
export JITTERSIM_ADMIN_TOKEN='use-a-long-local-token'
PYTHONPATH=src .venv/bin/python -m jittersim.api --database ./jittersim.db
```

Open <http://127.0.0.1:8080/>. For Pi setup, remote access and recovery, see
the [deployment guide](deploy/raspberry-pi/README.md).

## Run checks

Run the Python tests with:

```sh
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests
```

The Linux network checks use real firewall and traffic rules in disposable
network namespaces. Run them in a privileged Linux container with:

```sh
tests/kernel/run-in-docker.sh
```

The browser checks exercise the dashboard on desktop and phone-sized screens.
GitHub Actions runs the Python, Linux and browser checks for each pull
request.

## More information

- [Pi installation, security, recovery and hardware testing](deploy/raspberry-pi/README.md)
- [Measured effects of the profile presets](docs/measurements-2026-09.md)
- [Product roadmap and known limits](ROADMAP.md)
