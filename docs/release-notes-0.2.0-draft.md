This is the first signed release of JitterSim. The complete list of changes is in CHANGELOG.md.

## What you see in 0.2.0

- Update the Pi from the Updates page, with `sudo jittersim update` on the Pi or with `jittersim remote update now` from a Mac. Each update is checked against a release signing key the Pi trusts, keeps a recovery copy and checks the Pi's health afterwards. A failed health check restores the previous installation.
- Start one device on its own profile. Protected devices open the dashboard with the admin token and no extra step.
- The test Wi-Fi goes back to the router's name at every boot after a copy of the router Wi-Fi.
- Damaged, copied and reordered uploads never reach the household router. A router health check and logs that stay on disk help after an outage.
- Panic stays reachable when the dashboard is busy or the disk stalls.
- Questions are asked in the page, screen readers hear each result once and the dashboard has a new look.

## Upgrade notes

1. **A Pi that runs an older development build needs the root installer once.** Its update manager refuses packages from before the Panic review. Follow "Before the first release" in docs/releasing.md. The installer asks for the Pi's `sudo` password once. After that, updates need no `sudo`.
2. **Trust the signing key and save a read-only GitHub token on the Pi** (`sudo jittersim update trust-key` and `sudo jittersim update set-token`) so that Check for updates can find releases. The Pi installs only a release that a trusted key signed and that is newer than the one it runs.
3. An install or update sets Avahi's `disable-user-service-publishing` to `no`, so the `.local` name is announced, and keeps the journal on disk (up to 64 MB). A running test Wi-Fi is not restarted.
4. **After a rollback from this release to an older development build, reboot the Pi.** The older Panic and Reset cannot remove the upload corruption setup this release adds. If the status says the upload intake could not be removed, press Panic, check `tc qdisc show` on the test Wi-Fi interface and reboot.
5. A rollback restores all saved settings, including the protected device list, so remove again any device you stopped protecting after the update. The Pi also keeps a release-order high-water mark that a restore does not lower. After **Restore previous installation** it refuses this same package again and takes only a newer release.
6. Release owners: `scripts/release.sh X.Y.Z` checks, signs and publishes a release and asks before it merges or publishes. Hosted CI cannot start jobs, so the release carries a signed local verification record.

## Known limits

Physical acceptance on a real Pi with real phones is still outstanding. Router Wi-Fi is read only. A device cannot run its own profile while Everyone runs. The dashboard cannot show release notes yet, so read them with `sudo jittersim update`. See the known limits in docs/user-guide.md.
