# Changelog

Every JitterSim release. The newest is first. `scripts/release.sh` adds each
section from GitHub's generated release notes, and the same section travels
inside the signed release archive so the Pi can show it before installing.
Changes merged after the newest release are listed under Unreleased until the
next release takes them over. The Pi shows at most 16 KB of a section, so keep
the hand-written Unreleased part short.

## Unreleased

- The sidebar has a **Reset Wi-Fi name** button, so the Pi's Wi-Fi name can be restored from any page without opening settings. It uses the same confirmation and the same action as the Router and Wi-Fi page. The restart notice now says that every device on the Pi's Wi-Fi, including the one showing the dashboard, disconnects for a short time, and the page names the network to join again.

### JitterSim 0.2.0

The first signed JitterSim release adds safer updates with a recovery copy and health check, per-device profiles, protected-device dashboard access and router Wi-Fi name following. It also improves accessibility and reliability.

Security fixes block UDP source-port 53 traffic from test Wi-Fi clients through the Pi and limit each device to 16 of the browser proxy's 32 connection slots.

- The Connected clients list labels the device you are browsing with as **This device**.

See the [complete development changes](https://github.com/smartinezai/jittersim/blob/main/docs/history/0.2.0-development-changes.md).

### Changed

- Every profile preset now says what it is based on, in the form and in the user guide. Slow 3G (Chrome DevTools) and DSL are new. An 802.11ac Wi-Fi preset is not added: no first-hand source for its numbers was found.
