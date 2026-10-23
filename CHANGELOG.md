# Changelog

Every JitterSim release. The newest is first. `scripts/release.sh` adds each
section from GitHub's generated release notes, and the same section travels
inside the signed release archive so the Pi can show it before installing.
Changes merged after the newest release are listed under Unreleased until the
next release takes them over. The Pi shows at most 16 KB of a section, so keep
the hand-written Unreleased part short.

## Unreleased

- The global block list holds 2000 names instead of 100. Saving checks the whole household access policy first, and the website lookups no longer stall while many names are renewed.

### JitterSim 0.2.0

The first signed JitterSim release adds safer updates with a recovery copy and health check, per-device profiles, protected-device dashboard access and router Wi-Fi name following. It also improves accessibility and reliability.

Security fixes block UDP source-port 53 traffic from test Wi-Fi clients through the Pi and limit each device to 16 of the browser proxy's 32 connection slots.

See the [complete development changes](https://github.com/smartinezai/jittersim/blob/main/docs/history/0.2.0-development-changes.md).
