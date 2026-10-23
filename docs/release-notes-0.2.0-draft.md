## Upgrade notes

- A Pi that runs an older development build needs the root installer once, as described in [Before the first release](https://github.com/smartinezai/jittersim/blob/main/docs/releasing.md#before-the-first-release-a-pi-that-runs-a-development-build). After that, updates work from the dashboard or the command line.
- Before **Check for updates** works, save the trusted release signing key and a read-only GitHub token on the Pi. See [Signed releases](https://github.com/smartinezai/jittersim/blob/main/deploy/raspberry-pi/README.md#signed-releases).
- **Restore previous installation** brings back the settings saved at that time, including the protected-device list. A device you stopped protecting since the update may be protected again, and so may open the dashboard again. Remove it again.
- After a restore the Pi still remembers the newest release it installed, so the same package cannot be installed again. Only a newer release can.
- After a rollback from this release to an older development build, if the status says the upload intake could not be removed, press **Panic** and reboot the Pi if it is still there.

## Known limits

Tested on one Raspberry Pi 3 Model B Plus. Other Pi models and the capacity of a loaded Pi were not tested. JitterSim never turns the router's Wi-Fi on or off. See [known limits](https://github.com/smartinezai/jittersim/blob/main/docs/user-guide.md#known-limits).
