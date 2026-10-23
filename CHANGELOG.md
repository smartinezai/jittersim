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

- **Check connection** sends 100 ping probes from the Pi to a connected device and compares what comes back with its download profile. It does not judge the upload side or a speed cap.
- **Test speed** records a device's traffic while you run a speed test on it, to compare before and after a profile.
- **Address changes** suggests moving a saved device's settings when its private Wi-Fi address changes.
- **Apply profile** starts one device on its own profile, also while a profile for Everyone runs.
- A protected device opens the dashboard with the admin token alone.
- The device you browse with is marked **This device** in Connected clients.
- The sidebar has a **Reset Wi-Fi name** button. It restarts the Pi's Wi-Fi, so every device on it, possibly the one showing the dashboard, loses its connection for up to a minute.

Security fixes block UDP source-port 53 traffic from test Wi-Fi clients through the Pi and limit each device to 16 of the browser proxy's 32 connection slots.

See the [complete development changes](https://github.com/smartinezai/jittersim/blob/main/docs/history/0.2.0-development-changes.md).
