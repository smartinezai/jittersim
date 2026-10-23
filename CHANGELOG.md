# Changelog

Every JitterSim release. The newest is first. `scripts/release.sh` adds each
section from GitHub's generated release notes, and the same section travels
inside the signed release archive so the Pi can show it before installing.
Changes merged after the newest release are listed under Unreleased until the
next release takes them over. The Pi shows at most 16 KB of a section, so keep
the hand-written Unreleased part short.

## Unreleased

### JitterSim 0.2.0

The first signed JitterSim release adds one-step updates with a recovery copy and a health check, per-device profiles, protected-device dashboard access, router Wi-Fi name following and accessibility and reliability improvements.

What is new:

- **Check connection** sends a burst of ping probes (100 by default) from the Pi to a connected device and compares what comes back with its download profile. It does not judge the upload side or a speed cap. Its pings, like those of Test speed, stay on the test Wi-Fi network.
- **Test speed** records a device's traffic while you run a speed test on it, to compare before and after a profile. Opened on the device itself, the dashboard can also send it test data with no outside server (download only).
- **Address changes** asks before moving a saved device's settings when its private Wi-Fi address changes. Automatic and Off can be chosen.
- **Apply profile** starts one device on its own profile, also while a profile for Everyone runs.
- A protected device opens the dashboard with the admin token alone.
- The device you browse with over the test Wi-Fi is marked **This device** in Connected clients.
- The sidebar has a **Reset Wi-Fi name** button. It restarts the Pi's Wi-Fi, so every device on it, possibly the one showing the dashboard, loses its connection for up to a minute.
- The global block list holds 2000 names (80000 characters) instead of 100. An older release refuses more than 100 names.
- Each profile preset says what its numbers are based on. DSL and Slow 3G are new presets.
- `jittersim backup pull` on a Mac saves the Pi's database backup in `~/jittersim-backups/`.

Security fixes block UDP source-port 53 traffic from test Wi-Fi clients through the Pi and limit each device to 16 of the browser proxy's 32 connection slots.

See the [complete development changes](https://github.com/smartinezai/jittersim/blob/main/docs/history/0.2.0-development-changes.md).
