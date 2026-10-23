## Upgrade notes

- A Pi built before the current Panic protection review refuses this release with "The selected package cannot preserve Panic protection" and needs the root installer once. See [Before the first release](https://github.com/smartinezai/jittersim/blob/main/docs/releasing.md#before-the-first-release-a-pi-that-runs-a-development-build).
- Before **Check for updates** works, save the trusted release signing key and a read-only GitHub token on the Pi. See [Signed releases](https://github.com/smartinezai/jittersim/blob/main/deploy/raspberry-pi/README.md#signed-releases).
- **Restore previous installation** brings back the settings saved at that time, including the protected-device list. A device you stopped protecting since then may be protected, and may open the dashboard, again. Remove it again. A saved dashboard access grant you removed stays removed.
- After a restore the Pi still remembers the newest release it installed, so the same package cannot be installed again. Only a newer release can.
- Before you go back to an older release by hand, shorten the global block list to 100 names. An older release refuses a longer list: its dashboard does not start and the test network is closed at boot. Over SSH, `sudo jittersim normal-access` opens it again, and the dashboard stays down until you install the newer release again or empty the list by hand. **Restore previous installation** is not affected.
- After a rollback from this release to an older development build, if the status says the upload intake could not be removed, press **Panic** and reboot the Pi if it is still there.

## Known limits

Upload reordering and duplication are not applied, and the Pi has to offer an `ifb` device for upload corruption. Tested on one Raspberry Pi 3 Model B Plus. Other Pi models and the capacity of a loaded Pi were not tested. JitterSim never turns the router's Wi-Fi on or off. See [known limits](https://github.com/smartinezai/jittersim/blob/main/docs/user-guide.md#known-limits).
