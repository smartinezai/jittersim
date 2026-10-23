## Upgrade notes

- A Pi that runs an older development build needs the root installer once, as described in [Before the first release](https://github.com/smartinezai/jittersim/blob/main/docs/releasing.md#before-the-first-release-a-pi-that-runs-a-development-build). After that, updates work from the dashboard or the command line.
- Before **Check for updates** works, save the trusted release signing key and a read-only GitHub token on the Pi. See [Signed releases](https://github.com/smartinezai/jittersim/blob/main/deploy/raspberry-pi/README.md#signed-releases).
- **Restore previous installation** brings back the settings saved at that time, including the protected-device list. A device you stopped protecting since the update may be protected again, and so may open the dashboard again. Remove it again.
- Restoring does not lower the release-order high-water mark, so the same package cannot be installed again after a restore. Only a newer release can.
- After a rollback from this release to an older development build, reboot the Pi.

## Known limits

Physical acceptance on a real Pi with real phones remains outstanding. JitterSim never turns the router's Wi-Fi on or off. See [known limits](https://github.com/smartinezai/jittersim/blob/main/docs/user-guide.md#known-limits).
