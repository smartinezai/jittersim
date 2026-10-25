# Changelog

Every JitterSim release. The newest is first. `scripts/release.sh` adds each
section from GitHub's generated release notes, and the same section travels
inside the signed release archive so the Pi can show it before installing.
Changes merged after the newest release are listed under Unreleased until the
next release takes them over. The Pi shows at most 16 KB of a section, so keep
the hand-written Unreleased part short.

## v1.0.1 - 2026-10-25

This compatibility update prepares an existing 1.0.0 installation to accept the exact reviewed networking consumers in the forthcoming 1.1.0 update. The bridge retains the existing Panic guard, privileged helper and network-access code. Its updater adds one complete reviewed hash tuple while retaining all previous approved tuples.

It also includes the fixes already merged after the 1.0.0 candidate: page tabs preserve the header and navigation, the keyboard skip link stays hidden when unfocused at enlarged text sizes, the router Wi-Fi Check action is visible, and CSV exports preserve embedded carriage returns on Python 3.9. Documentation and the corresponding test fixtures have been corrected.

Install this bridge before 1.1.0 and confirm that the bridge is running. A 1.0.0 updater cannot skip directly to the new networking consumers; it refuses that package before changing the running installation. Rolling back to the older updater can require installing this bridge again.

Packages remain signed and verified. Check for updates downloads and stages the latest published stable release; Install remains a separate confirmed action. An unpublished acceptance candidate is not discoverable through the latest-release check.

This release does not diagnose or enable unavailable upload-corruption kernel capabilities on the Pi. No new real-device acceptance is claimed by these notes; approval requires evidence for this exact signed package.

## v1.0.0 - 2026-10-24

### JitterSim 1.0.0

This release brings together the reviewed dashboard and device-control changes:

- Operate uses full-width navigation, compact controls and a persistent safety bar.
- Prepared device profiles remain inactive until an explicit Apply. Manual Apply
  requires fresh verified device identity before saving or starting a profile.
- Discovery results are easier to scan, notifications preserve newer feedback
  and profile resets and device controls make the next action clearer.
- Traffic charts scale from measured rates, identify limits above the chart and
  offer sampling pause and a bounded table of recent measurements.
- The local CI harness includes its documented container prerequisites and the
  prepared-device browser journey. A focused corruption-effect test adds evidence
  for the existing network effect.

The existing signed stable-release update flow remains available. A dashboard
workflow for unpublished release candidates is not included.

Real Pi and Safari acceptance of this combined candidate remain outstanding.
These notes do not establish package creation or physical acceptance.

The documented version policy requires a major release when saved settings
change or the full installer is required. Prepared choices add saved storage.
Older-code compatibility was tested but does not waive that policy. PR175 and
the separately held protected bootstrap changes are not included.

## v0.2.0 - 2026-10-24

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

### Upgrade notes

- A Pi built before the current Panic protection review refuses this release with "The selected package cannot preserve Panic protection" and needs the root installer once. See [Before the first release](https://github.com/smartinezai/jittersim/blob/main/docs/releasing.md#before-the-first-release-a-pi-that-runs-a-development-build).
- Before **Check for updates** works, save the trusted release signing key and a read-only GitHub token on the Pi. See [Signed releases](https://github.com/smartinezai/jittersim/blob/main/deploy/raspberry-pi/README.md#signed-releases).
- **Restore previous installation** brings back the settings saved at that time, including the protected-device list. A device you stopped protecting since then may be protected, and may open the dashboard, again. Remove it again. A saved dashboard access grant you removed stays removed.
- After a restore the Pi still remembers the newest release it installed, so the same package cannot be installed again. Only a newer release can.
- Before you go back to an older release by hand, shorten the global block list to 100 names. An older release refuses a longer list: its dashboard does not start and the test network is closed at boot. Over SSH, `sudo jittersim normal-access` opens it again, and the dashboard stays down until you install the newer release again or empty the list by hand. **Restore previous installation** is not affected.
- After a rollback from this release to an older development build, if the status says the upload intake could not be removed, press **Panic** and reboot the Pi if it is still there.

### Known limits

Upload reordering and duplication are not applied, and the Pi has to offer an `ifb` device for upload corruption. Tested on one Raspberry Pi 3 Model B Plus. Other Pi models and the capacity of a loaded Pi were not tested. JitterSim never turns the router's Wi-Fi on or off. See [known limits](https://github.com/smartinezai/jittersim/blob/main/docs/user-guide.md#known-limits).

