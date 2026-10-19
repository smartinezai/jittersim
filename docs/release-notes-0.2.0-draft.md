# JitterSim 0.2.0 release notes (draft)

This is the first signed release of JitterSim. It adds signed updates with a recovery copy and health check, per-device profiles, protected-device dashboard access, router Wi-Fi name following and accessibility and reliability improvements. Security fixes block test Wi-Fi UDP source-port 53 traffic through the Pi and limit each device to 16 of the proxy's 32 connections. See [all development changes](https://github.com/smartinezai/jittersim/blob/main/docs/history/0.2.0-development-changes.md).

## Upgrade notes

- Older development builds need the root installer once, as described in [Before the first release](https://github.com/smartinezai/jittersim/blob/main/docs/releasing.md#before-the-first-release-a-pi-that-runs-a-development-build).
- Save the trusted release signing key and a read-only GitHub token on the Pi before checking for releases.
- Rollback restores saved settings, including the protected-device list. Devices unprotected since the update may be protected again and need removing again. Rollback does not lower the release-order high-water mark, so the same package cannot be installed again after restore.
- After rollback from this release to an older development build, reboot the Pi.

## Known limits

Physical acceptance on a real Pi with real phones remains outstanding. Router Wi-Fi is read only. See [known limits](https://github.com/smartinezai/jittersim/blob/main/docs/user-guide.md#known-limits).
