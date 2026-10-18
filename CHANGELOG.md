# Changelog

Every JitterSim release. The newest is first. `scripts/release.sh` adds each
section from GitHub's generated release notes, and the same section travels
inside the signed release archive so the Pi can show it before installing.
Changes merged after the newest release are listed under Unreleased until the
next release takes them over. The Pi shows at most 16 KB of a section, so keep
the hand-written Unreleased part short.

## Unreleased

### Added

- Update the Pi in one step. `sudo jittersim update` on the Pi (add `--yes` to
  skip the question or `--offline` to skip GitHub) and `jittersim remote update
  now` from a Mac check GitHub for a newer signed release, show its notes,
  install it and report success only when the install request reached
  `complete` and the Pi runs the new version. A rollback is reported only when
  the Pi confirmed it. An update that is already running is followed instead of
  started again, and under `--yes` a release that just failed is not installed
  again. `jittersim remote update now --json` prints one JSON answer (it cannot
  ask, so it needs `--yes`). `jittersim version` prints the version the Pi runs.
- The Updates page names the staged release on its install button (**Install
  v1.1.0**), shows whether a recovery copy is kept and who signed the release,
  and the install confirmation names the signer and the automatic restore. The
  page can show release notes once the Pi's update status carries them. Until
  then `sudo jittersim update` shows the notes.
- Apply a saved profile to one device. After **Reset network**, **Apply profile**
  starts only that device and everything else stays paused. While Everyone runs
  it is refused before anything is saved.
- A protected device opens the dashboard with the admin token and no separate
  **Allow this device** step. The access list says whether access comes from
  protection, a saved grant or both. Stopping protection of the device in use
  asks whether to keep its access first. SSH and router access are not granted.
- The Pi Wi-Fi follows the router. After a **Copy router Wi-Fi to Pi** that
  worked, the Pi records the router's Wi-Fi name and security (no password).
  Every boot renames a saved Wi-Fi with another name, such as
  `JitterSim-Recovery`, back to the router name. The router is not contacted.
  If the router name does not start, the previous name comes back for that
  boot. A manual Wi-Fi change or a name reset stops the following.
  `sudo /opt/jittersim/.venv/bin/python -m jittersim.networking.wifi_follow`
  `recovery` holds Recovery across reboots, `router` returns now and `status`
  names the state.
- A router health check. Once a minute the Pi reads the router's ARP entry,
  sends one ping and asks its DNS for one name. It never logs in. After two
  failures in a row the journal gets one record. After an install or update the
  journal stays on disk (up to 64 MB for 7 days), so an outage leaves evidence
  after a reboot.
- A skip link to the main content, unique names for the page regions and a name
  for the actions column of the device table.
- One command cuts, checks, signs and publishes a release: `scripts/release.sh
  X.Y.Z`. `scripts/local-ci.sh` runs the workflow checks on a Mac while hosted
  CI cannot start.

### Changed

- The dashboard has a new look: layered dark surfaces, a type scale, an icon
  beside each page link, a logo mark and short motion that stops under reduced
  motion. Nothing behaves differently.
- The dashboard asks before deleting a profile, device or group, clearing the
  activity log, importing settings, changing the Pi Wi-Fi, restoring its name,
  reversing an address move, copying the router Wi-Fi, removing dashboard
  access or stopping protection of the device in use, in its own dialog instead
  of the browser's. The dialog names the action on its button, Escape cancels it
  and focus returns to the control that opened it.
- A screen reader hears each result once. The dashboard has one status region
  and one alert region, and lists that refresh every few seconds (devices,
  schedules, timers, limits, observations and the router connection) no longer
  announce themselves. A refresh keeps keyboard focus on the button you were on.
- Router Wi-Fi is read only. The Pi refuses every router Wi-Fi write and the
  switch is disabled. Read-only checks and **Copy router Wi-Fi to Pi** still
  work. Turn the router Wi-Fi on or off with the router's own button or page.
  Router password checks are spaced 15 seconds apart after a right password and
  30 seconds after a wrong one.
- Upload shaping keeps damaged, copied and reordered packets away from the
  household router. Upload delay, jitter, loss and rate stay on the Ethernet
  port. Upload corruption runs on an `ifb` device before the Pi forwards the
  packet. Upload reordering and duplication are not applied (downloads keep all
  settings). The Apply message and the profile form say so.
- The test Wi-Fi's DHCP is authoritative. A device that still holds a router
  address is told to ask again and gets a fresh one, and leases last 5 minutes.
  A device that joins the copied router name gets internet instead of staying on
  an address that does not work.
- Panic stays reachable and bounded. The dashboard keeps two workers and the
  helper one slot for Panic, an idle connection cannot starve it, a stop
  survives a helper restart, the household release opens the firewall first and
  never goes back to the restrictive rules, and one total deadline bounds the
  stop. A stalled disk write no longer holds Panic back: it removes the rules,
  releases the household and says the result is not confirmed.
- A rollback keeps the current `dashboard-access.json`, so a revoked dashboard
  grant does not come back. A rollback still restores all saved settings,
  including the protected device list.

### Fixed

- The Pi's `.local` name is announced again. Avahi's setting
  `disable-user-service-publishing` was `yes`, which made Avahi refuse the name
  service's records, so nothing was ever announced. An install or update sets it
  to `no` and restarts Avahi. The name service also survives a hangup signal in
  the first second of boot and restarts after any exit.
- A failed check on GitHub is an error instead of "Up to date".
- A save, an Apply or a delete no longer makes the dashboard read out its
  loading notes and device count again. Cancelling a question gives focus back to
  the button that asked, also when its list was drawn again while the question
  was open. Escape in a question no longer clears a highlighted traffic line.
- The schedule, timer, progression and limit forms are fixed where tests found
  defects: shared group limits are listed, a hidden field with a bad value no
  longer blocks a save in silence, **Discard unfinished edits** resets every
  field and "1 minutes" now says "1 minute".
- Odd input gets a plain 400 error instead of a server error: a number with more
  than 4300 digits (on every Python version), an identifier that is not text, a
  lone surrogate character and a settings version outside the database range.
- The dashboard stops cleanly when systemd stops it. A status poll no longer
  discards a running device scan, which showed as an error on the device list.
- **Reset network** works when the Wi-Fi interface is missing.
- Protected devices stay out of the upload corruption path, also on connections
  that were already open.
- A failed device-only cleanup stays owed until it works. If the installer
  cannot remove the upload corruption setup it starts the unchanged installation
  again and stops with an error. A rollback from the first release that has the
  upload-safe layout to an older release can leave that setup in place. The final
  status then warns. Reboot the Pi after such a warning.
